#!/usr/bin/env node
// Records the toulouse-distorama-slideshow map as a 1080×1920 MP4 (YouTube Shorts).
// Video track: Playwright recordVideo (real browser session with local <video> playback).
// Audio track: ffmpeg composite built from downloaded source clips.
// Timestamp track: CSV log of event start times.
//
// Usage:
//   node scripts/toulouse-distorama/capture-slideshow.js [output.mp4]
//   node scripts/toulouse-distorama/capture-slideshow.js --remix <state.json> [options]
//
// Remix options (skip browser capture, redo audio mix):
//   --clip-offset <s>          Seconds into each source clip to start (default: 30)
//   --fade-out <s>             Audio fade-out duration per slide (default: 2)
//   --intro-dur <s>            Intro silence duration (default: 3)
//   --outro-dur <s>            Outro silence duration (default: 5)
//   --output <path>            Write remix to this path instead of the original
//   --timestamp-offsets <csv>  Batch mode: comma-separated offsets (s) added to intro silence
//                              e.g. -1.5,-1,-0.5,0,0.5,1,1.5 → 7 output files named *_ts-1p5.mp4 etc.
//   --youtube-url <url>        YouTube URL whose audio fills intro (fade-in) and outro (fade-out); omit for silence

const { chromium } = require('playwright');
const { spawnSync, spawn } = require('child_process');
const fs   = require('fs');
const path = require('path');
const crypto = require('crypto');

const ROOT            = path.resolve(__dirname, '../..');
const GEOJSON         = path.join(ROOT, 'static/toulouse-distorama/events/this-week.geojson');
const TEMP_GEOJSON    = path.join(ROOT, 'static/toulouse-distorama/events/this-week-capture.geojson');
const CAPTURE_GEOJSON = '/toulouse-distorama/events/this-week-capture.geojson';
const TMP_MEDIA_DIR   = path.join(ROOT, 'static/toulouse-distorama/tmp-media');
const VIDEO_DIR       = '/tmp/distorama-video';
const OUTPUT_DIR      = path.join(ROOT, 'static/toulouse-distorama/slideshows');
const PORT            = 1399;
const URL             = `http://localhost:${PORT}/toulouse-distorama-slideshow/?geojson=${CAPTURE_GEOJSON}`;


// ── CLI parsing ────────────────────────────────────────────────────────────
const args = process.argv.slice(2);

function getFlag(flag) {
  const i = args.indexOf(flag);
  return i !== -1 ? args[i + 1] : null;
}

const REMIX_STATE           = getFlag('--remix');
const CLI_CLIP_OFFSET       = getFlag('--clip-offset');
const CLI_FADE_OUT          = getFlag('--fade-out');
const CLI_INTRO_DUR         = getFlag('--intro-dur');
const CLI_OUTRO_DUR         = getFlag('--outro-dur');
const CLI_OUTPUT            = getFlag('--output');
const CLI_TIMESTAMP_OFFSETS = getFlag('--timestamp-offsets');
const CLI_YOUTUBE_URL       = getFlag('--youtube-url');

// ── Constants (overridable on remix) ──────────────────────────────────────
// Must match layout constants for fresh captures; remixes may deviate.
const INTRO_DUR    = parseFloat(CLI_INTRO_DUR  ?? 3);
const OUTRO_DUR    = parseFloat(CLI_OUTRO_DUR  ?? 5);
const SLIDE_DUR    = 5;
const FADE_OUT_DUR = parseFloat(CLI_FADE_OUT   ?? 2);
const CLIP_OFFSET  = parseFloat(CLI_CLIP_OFFSET ?? 30);

function getWeekNumber(d) {
  d = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  d.setUTCDate(d.getUTCDate() + 4 - (d.getUTCDay() || 7));
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  return Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
}

const HASH   = crypto.randomBytes(4).toString('hex');
const WEEK   = getWeekNumber(new Date());
const BASENAME   = `distorama-week-${WEEK}_${HASH}`;
const OUTPUT_MP4 = path.join(OUTPUT_DIR, `${BASENAME}.mp4`);
const OUTPUT_CSV = path.join(OUTPUT_DIR, `${BASENAME}.csv`);
const STATE_FILE = path.join(OUTPUT_DIR, `${BASENAME}.state.json`);

// ── 1. Parse GeoJSON → sorted slide list ──────────────────────────────────
function loadSlides() {
  const data = JSON.parse(fs.readFileSync(GEOJSON, 'utf8'));
  const rows = [];
  data.features.forEach(feat => {
    (feat.properties.events || []).forEach(ev => {
      if (ev.youtube_video_id) rows.push({ feat, ev });
    });
  });
  rows.sort((a, b) =>
    (a.ev.date || '').localeCompare(b.ev.date || '') ||
    a.feat.properties.name.localeCompare(b.feat.properties.name)
  );
  return rows;
}

// ── 2. Download YouTube Media (360p MP4) ──────────────────────────────────
function downloadMedia(videoId) {
  const base = path.resolve(TMP_MEDIA_DIR);
  const dest = path.resolve(base, `${videoId}.mp4`);
  const rel = path.relative(base, dest);
  if (rel.startsWith('..') || path.isAbsolute(rel)) { console.warn(`  ⚠ invalid videoId`); return null; }
  if (fs.existsSync(dest) && fs.statSync(dest).size > 10000) return dest;
  console.log(`  ↓ downloading ${videoId}…`);
  const r = spawnSync('yt-dlp', [
    '--js-runtimes', 'node',
    '-f', '18',
    '--no-playlist',
    '--quiet', '--no-warnings',
    '-o', dest,
    `https://www.youtube.com/watch?v=${videoId}`,
  ], { stdio: ['ignore', 'pipe', 'pipe'] });
  if (r.status !== 0) { console.warn(`  ⚠ yt-dlp failed for ${videoId}`); return null; }
  if (!fs.existsSync(dest) || fs.statSync(dest).size < 10000) {
    console.warn(`  ⚠ download produced invalid file for ${videoId}`); return null;
  }
  return dest;
}

// ── 3. Download audio-only (MP3) ──────────────────────────────────────────
function downloadAudioOnly(url, dest) {
  if (fs.existsSync(dest) && fs.statSync(dest).size > 10000) return dest;
  console.log(`  ↓ downloading audio ${path.basename(dest)}…`);
  const r = spawnSync('yt-dlp', [
    '--js-runtimes', 'node',
    '-x', '--audio-format', 'mp3',
    '--no-playlist', '--quiet', '--no-warnings',
    '-o', dest, url,
  ], { stdio: ['ignore', 'pipe', 'pipe'] });
  if (r.status !== 0 || !fs.existsSync(dest) || fs.statSync(dest).size < 10000) {
    console.warn(`  ⚠ audio download failed: ${url}`); return null;
  }
  return dest;
}

// ── Helpers ────────────────────────────────────────────────────────────────
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function waitFor(fn, timeoutMs = 20000, intervalMs = 500) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await fn()) return;
    await sleep(intervalMs);
  }
  throw new Error('waitFor timed out');
}

function loadEnv() {
  const env = { ...process.env };
  const envFile = path.join(ROOT, '.env');
  if (fs.existsSync(envFile)) {
    fs.readFileSync(envFile, 'utf8').split('\n').forEach(line => {
      const m = line.match(/^([^#=\s][^=]*)=(.*)$/);
      if (m) env[m[1].trim()] = m[2].trim().replace(/^["']|["']$/g, '');
    });
  }
  return env;
}

// ── Audio mix (steps 11–12) ────────────────────────────────────────────────
function offsetOutputPath(baseMp4, offset) {
  const tag = `_ts${offset}`.replace('.', 'p');
  return baseMp4.replace(/\.mp4$/, `${tag}.mp4`);
}

function buildAudio({ timestampLog, totalDur, outroTimestamp, mediaMap, videoPath, videoStartOffset, outputMp4, timestampOffset = -1.5, youtubeFillerPath = null }) {
  const compositeAudioPath = '/tmp/distorama-composite-audio.m4a';
  try { fs.unlinkSync(compositeAudioPath); } catch (e) {}

  const audioInputs    = [];
  const filterParts    = [];
  const concatSegments = [];
  const silFmt = 'aformat=sample_rates=44100:channel_layouts=stereo';

  const introSilDur = Math.max(0, (timestampLog.length > 0 ? timestampLog[0].timestamp : INTRO_DUR) + timestampOffset);

  // ── Intro ──
  if (youtubeFillerPath && fs.existsSync(youtubeFillerPath)) {
    audioInputs.push('-i', youtubeFillerPath);
    const idx = (audioInputs.length / 2) - 1;
    filterParts.push(
      `[${idx}:a]atrim=start=30:end=${(30 + introSilDur).toFixed(3)},` +
      `asetpts=PTS-STARTPTS,afade=t=in:st=0:d=1,aresample=44100,${silFmt}[intro_audio]`
    );
    concatSegments.push('[intro_audio]');
  } else {
    filterParts.push(`aevalsrc=0:c=stereo:s=44100:d=${introSilDur.toFixed(3)},${silFmt}[intro_sil]`);
    concatSegments.push('[intro_sil]');
  }

  // ── Venue slides ──
  timestampLog.forEach((log, i) => {
    const resolvedBase = path.resolve(TMP_MEDIA_DIR);
    const resolvedTarget = path.resolve(resolvedBase, `${log.video_id}.mp4`);
    const relativePath = path.relative(resolvedBase, resolvedTarget);
    if (relativePath.startsWith('..') || path.isAbsolute(relativePath)) {
      throw new Error('Invalid file path');
    }
    const src = mediaMap[log.video_id] || resolvedTarget;
    const nextTs = timestampLog[i + 1]
      ? timestampLog[i + 1].timestamp
      : (outroTimestamp ?? (totalDur - OUTRO_DUR));
    const dur = Math.max(0.1, nextTs - log.timestamp);
    const label = `seg${concatSegments.length}`;

    if (!fs.existsSync(src)) {
      filterParts.push(`aevalsrc=0:c=stereo:s=44100:d=${dur.toFixed(3)},${silFmt}[${label}]`);
    } else {
      audioInputs.push('-i', src);
      const inIdx = (audioInputs.length / 2) - 1;
      const fadeOutSt = Math.max(0, dur - FADE_OUT_DUR).toFixed(3);
      filterParts.push(
        `[${inIdx}:a]atrim=start=${CLIP_OFFSET}:end=${(CLIP_OFFSET + dur).toFixed(3)},` +
        `asetpts=PTS-STARTPTS,` +
        `afade=t=in:st=0:d=1,afade=t=out:st=${fadeOutSt}:d=${FADE_OUT_DUR},` +
        `aresample=44100,${silFmt}[${label}]`
      );
    }
    concatSegments.push(`[${label}]`);
  });

  // ── Outro ──
  if (youtubeFillerPath && fs.existsSync(youtubeFillerPath)) {
    const outroStart = (30 + introSilDur).toFixed(3);
    const outroEnd   = (30 + introSilDur + OUTRO_DUR).toFixed(3);
    audioInputs.push('-i', youtubeFillerPath);
    const idx = (audioInputs.length / 2) - 1;
    filterParts.push(
      `[${idx}:a]atrim=start=${outroStart}:end=${outroEnd},` +
      `asetpts=PTS-STARTPTS,afade=t=out:st=${Math.max(0, OUTRO_DUR - 2).toFixed(3)}:d=2,` +
      `aresample=44100,${silFmt}[outro_audio]`
    );
    concatSegments.push('[outro_audio]');
  } else {
    filterParts.push(`aevalsrc=0:c=stereo:s=44100:d=${OUTRO_DUR},${silFmt}[outro_sil]`);
    concatSegments.push('[outro_sil]');
  }
  filterParts.push(`${concatSegments.join('')}concat=n=${concatSegments.length}:v=0:a=1[aout]`);

  console.log(`  Concatenating ${concatSegments.length} audio segments…`);
  const audioGen = spawnSync('ffmpeg', [
    '-y', ...audioInputs,
    '-filter_complex', filterParts.join(';'),
    '-map', '[aout]',
    '-c:a', 'aac', '-b:a', '192k',
    compositeAudioPath,
  ], { stdio: ['ignore', 'inherit', 'inherit'] });

  if (audioGen.status !== 0 || !fs.existsSync(compositeAudioPath) || fs.statSync(compositeAudioPath).size < 100) {
    console.error('✗ Composite audio generation failed — aborting.');
    process.exit(1);
  }
  console.log(`✓ Composite audio built: ${Math.round(fs.statSync(compositeAudioPath).size / 1024)} KB`);

  console.log(`\nEncoding final video… (trimming ${videoStartOffset.toFixed(2)}s pre-slideshow offset)`);
  const enc = spawnSync('ffmpeg', [
    '-y',
    '-ss', videoStartOffset.toFixed(3),
    '-i', videoPath,
    '-i', compositeAudioPath,
    '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black',
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '20', '-pix_fmt', 'yuv420p',
    '-c:a', 'aac', '-b:a', '192k',
    '-map', '0:v:0', '-map', '1:a:0',
    '-movflags', '+faststart',
    outputMp4,
  ], { stdio: 'inherit' });
  if (enc.status !== 0) process.exit(enc.status || 1);

  const kb = Math.round(fs.statSync(outputMp4).size / 1024);
  console.log(`\n✓ ${outputMp4} (${kb} KB)`);
}

function updateSymlinks(mp4Path, csvPath) {
  const LATEST_MP4 = path.join(OUTPUT_DIR, 'distorama-week-latest.mp4');
  const LATEST_CSV = path.join(OUTPUT_DIR, 'distorama-week-latest.csv');
  [LATEST_MP4, LATEST_CSV].forEach(f => { try { fs.unlinkSync(f); } catch (e) {} });
  try {
    fs.symlinkSync(path.basename(mp4Path), LATEST_MP4);
    if (csvPath) fs.symlinkSync(path.basename(csvPath), LATEST_CSV);
    console.log(`✓ Symlinks updated: distorama-week-latest.mp4/csv`);
  } catch (e) {
    console.warn(`⚠ Failed to create symlinks: ${e.message}`);
  }
}

// ── Remix mode ─────────────────────────────────────────────────────────────
async function remix() {
  const state = JSON.parse(fs.readFileSync(REMIX_STATE, 'utf8'));

  const youtubeFillerPath = CLI_YOUTUBE_URL
    ? downloadAudioOnly(CLI_YOUTUBE_URL, path.join(TMP_MEDIA_DIR, `filler_${CLI_YOUTUBE_URL.match(/[?&]v=([^&]+)/)?.[1] || 'url'}.mp3`))
    : (state.youtubeFillerPath || null);

  if (CLI_TIMESTAMP_OFFSETS) {
    const offsets = CLI_TIMESTAMP_OFFSETS.split(',').map(Number);
    console.log(`Batch remix from state: ${REMIX_STATE}`);
    console.log(`  timestamp offsets: ${offsets.map(o => (o >= 0 ? '+' : '') + o + 's').join('  ')}`);
    let lastMp4 = null;
    for (const offset of offsets) {
      const outputMp4 = offsetOutputPath(state.outputMp4, offset);
      console.log(`\n── offset ${offset >= 0 ? '+' : ''}${offset}s → ${path.basename(outputMp4)}`);
      buildAudio({ ...state, outputMp4, timestampOffset: offset, youtubeFillerPath });
      lastMp4 = outputMp4;
    }
    updateSymlinks(lastMp4, state.outputCsv);
    return;
  }

  const outputMp4 = CLI_OUTPUT || state.outputMp4;
  console.log(`Remixing from state: ${REMIX_STATE}`);
  console.log(`  clip-offset=${CLIP_OFFSET}  fade-out=${FADE_OUT_DUR}  intro=${INTRO_DUR}  outro=${OUTRO_DUR}`);
  console.log(`  output: ${outputMp4}`);
  console.log('\nBuilding high-quality composite audio…');
  buildAudio({ ...state, outputMp4, youtubeFillerPath });
  updateSymlinks(outputMp4, state.outputCsv);
}

// ── Main (fresh capture) ───────────────────────────────────────────────────
async function main() {
  [TMP_MEDIA_DIR, VIDEO_DIR, OUTPUT_DIR].forEach(d => fs.mkdirSync(d, { recursive: true }));
  const resolvedVideoDir = path.resolve(VIDEO_DIR); fs.readdirSync(resolvedVideoDir).filter(f => { const resolvedTarget = path.resolve(resolvedVideoDir, f); const rel = path.relative(resolvedVideoDir, resolvedTarget); if (rel.startsWith('..') || path.isAbsolute(rel)) return false; return f.endsWith('.webm'); }).forEach(f => { const resolvedTarget = path.resolve(resolvedVideoDir, f); const rel = path.relative(resolvedVideoDir, resolvedTarget); if (!rel.startsWith('..') && !path.isAbsolute(rel)) fs.unlinkSync(resolvedTarget); });

  const allSlides = loadSlides();
  console.log(`${allSlides.length} slides:`, allSlides.map(s => s.ev.youtube_video_id).join(' '));

  console.log('\nDownloading media…');
  const uniqueVideoIds = [...new Set(allSlides.map(s => s.ev.youtube_video_id))];
  const mediaMap = {};
  uniqueVideoIds.forEach(id => { mediaMap[id] = downloadMedia(id); });
  const youtubeFillerPath = CLI_YOUTUBE_URL
    ? downloadAudioOnly(CLI_YOUTUBE_URL, path.join(TMP_MEDIA_DIR, `filler_${CLI_YOUTUBE_URL.match(/[?&]v=([^&]+)/)?.[1] || 'url'}.mp3`))
    : null;
  const allMediaFiles = allSlides.map(s => mediaMap[s.ev.youtube_video_id]);

  const slides     = allSlides.filter((_, i) => !!allMediaFiles[i]);
  const dropped    = allSlides.length - slides.length;
  if (dropped > 0) {
    const skipped = allSlides.filter((_, i) => !allMediaFiles[i]).map(s => s.ev.youtube_video_id);
    console.log(`  ⚠ skipping ${dropped} slide(s) with failed download: ${skipped.join(' ')}`);
  }
  if (!slides.length) { console.error('No slides with media — aborting.'); process.exit(1); }

  const failedIds  = new Set(allSlides.filter((_, i) => !allMediaFiles[i]).map(s => s.ev.youtube_video_id));
  const rawData    = JSON.parse(fs.readFileSync(GEOJSON, 'utf8'));
  const filteredData = {
    ...rawData,
    features: rawData.features.map(f => ({
      ...f,
      properties: {
        ...f.properties,
        events: (f.properties.events || []).filter(ev =>
          !ev.youtube_video_id || !failedIds.has(ev.youtube_video_id)
        ),
      },
    })),
  };
  fs.writeFileSync(TEMP_GEOJSON, JSON.stringify(filteredData));

  console.log('\nStarting Hugo…');
  const env  = loadEnv();
  const hugo = spawn('hugo', ['server', '--port', String(PORT), '--disableFastRender', '--quiet', '--watch=false'], {
    cwd: ROOT, env, stdio: ['ignore', 'pipe', 'pipe'],
  });
  hugo.stderr.on('data', d => process.stderr.write(d));

  await waitFor(() => {
    const r = spawnSync('curl', ['-s', '-o', '/dev/null', '-w', '%{http_code}', URL], { timeout: 2000 });
    return r.stdout?.toString().trim() === '200';
  }, 25000, 500);
  console.log('Hugo ready.');
  await sleep(1000);

  const totalDur = INTRO_DUR + slides.length * (SLIDE_DUR + FADE_OUT_DUR) + OUTRO_DUR;
  console.log(`\nRecording browser session (${totalDur}s)…`);

  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--autoplay-policy=no-user-gesture-required'],
  });

  const recordingStartTime = Date.now();
  const ctx = await browser.newContext({
    viewport: { width: 540, height: 960 },
    isMobile: true,
    deviceScaleFactor: 1,
    userAgent: 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
    recordVideo: { dir: VIDEO_DIR, size: { width: 540, height: 960 } },
  });

  const page = await ctx.newPage();
  page.on('console', msg => console.log(`BROWSER: ${msg.text()}`));
  await page.goto(URL, { waitUntil: 'domcontentloaded' });

  console.log('Waiting for slideshow data…');
  await page.waitForFunction(() => window._ssReady === true, { timeout: 15000 });
  await sleep(3000);

  await page.click('.start-btn');
  const sessionStartTime = Date.now();
  const videoStartOffset = (sessionStartTime - recordingStartTime) / 1000;
  console.log('Slideshow started. Recording…');

  const monitoringDeadline = Date.now() + (10 + slides.length * 16 + 30) * 1000;
  let lastReportedSlide = -1;
  while (Date.now() < monitoringDeadline) {
    const phase = await page.evaluate(() => window._ss?.phase);
    if (phase === 'done') break;
    const currentSlide = await page.evaluate(() => window._ss ? window._ss.currentSlide : -1);
    if (currentSlide !== lastReportedSlide && currentSlide >= 0) {
      lastReportedSlide = currentSlide;
      process.stdout.write(`  [${currentSlide + 1}/${slides.length}] slide\r`);
    }
    await sleep(200);
  }
  console.log('\nSlideshow finished.');

  const slideLog     = await page.evaluate(() => window._ss.slideLog);
  const ssStartMs    = await page.evaluate(() => window._ss.startTime);
  const outroStartMs = await page.evaluate(() => window._ss.outroStartMs);
  const outroTimestamp = outroStartMs ? (outroStartMs - ssStartMs) / 1000 : null;
  const timestampLog = slideLog.map(entry => ({
    timestamp: (entry.t - ssStartMs) / 1000,
    artist:    entry.ev.artist || entry.ev.desc || '',
    venue:     entry.feat.properties.name,
    video_id:  entry.ev.youtube_video_id,
  }));

  const csvContent = [
    'timestamp,artist,venue,video_id',
    ...timestampLog.map(l => `${l.timestamp.toFixed(2)},"${l.artist.replace(/"/g, '""')}","${l.venue.replace(/"/g, '""')}",${l.video_id}`)
  ].join('\n');
  fs.writeFileSync(OUTPUT_CSV, csvContent);
  console.log(`✓ CSV record saved: ${OUTPUT_CSV}`);

  const videoPath = await page.video().path();
  await ctx.close();
  await browser.close();
  hugo.kill();
  try { fs.unlinkSync(TEMP_GEOJSON); } catch (e) {}

  // Save capture state for potential remix
  const state = { videoPath, videoStartOffset, timestampLog, totalDur, outroTimestamp, mediaMap, youtubeFillerPath, outputMp4: OUTPUT_MP4, outputCsv: OUTPUT_CSV };
  fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2));
  console.log(`✓ Capture state saved: ${STATE_FILE}`);

  console.log('\nBuilding high-quality composite audio…');
  buildAudio(state);
  updateSymlinks(OUTPUT_MP4, OUTPUT_CSV);
}

// ── Entry point ────────────────────────────────────────────────────────────
if (REMIX_STATE) {
  remix().catch(err => { console.error(err); process.exit(1); });
} else {
  main().catch(err => { console.error(err); process.exit(1); });
}
