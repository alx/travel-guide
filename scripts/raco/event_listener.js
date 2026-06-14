const { chromium } = require('playwright');

(async () => {
  // Launch in non-headless mode so you can see what's happening
  const browser = await chromium.launch({ headless: false }); 
  const page = await browser.newPage();

  const targetString = "LonSkii";
  console.log(`🕵️ Listening for "${targetString}" in all network traffic...`);

  // Intercept every response the browser receives
  page.on('response', async (response) => {
    const url = response.url();
    const type = response.request().resourceType();

    // We only care about data requests (Fetch/XHR) or the initial HTML document
    if (type === 'fetch' || type === 'xhr' || type === 'document') {
      try {
        const text = await response.text();

        // Check if the payload contains your specific artist/event string
        if (text.includes(targetString)) {
          console.log('\n--------------------------------------------------');
          console.log(`🎯 BINGO! Found "${targetString}"!`);
          console.log(`🔗 Source URL: ${url}`);
          console.log(`📦 Resource Type: ${type}`);

          // Analyze the type of data we found
          if (type === 'document') {
            console.log('🧠 Result: The data is embedded directly in the initial HTML load.');
            console.log('💡 Tip: Look for a <script> tag like __NEXT_DATA__ or Apollo state at the bottom of the HTML.');
          } else if (text.trim().startsWith('{') || text.trim().startsWith('[')) {
            console.log('🧠 Result: It is a dynamic JSON/API response!');
            // Print a small snippet of the JSON to verify
            const snippet = text.substring(0, 300).replace(/\s+/g, ' ') + '...';
            console.log(`📄 Snippet: ${snippet}`);
          }
        }
      } catch (error) {
        // Safely ignore responses that can't be read as text (like opaque CORS requests)
      }
    }
  });

  console.log('Navigating to Resident Advisor...');
  
  // Go to the page and wait until the network is mostly quiet
  await page.goto('https://ra.co/events/th/bangkok', { waitUntil: 'networkidle' });

  console.log('Page loaded. Waiting 5 seconds just in case there are delayed scripts...');
  await page.waitForTimeout(5000);

  console.log('\nDone listening. Closing browser.');
  await browser.close();
})();
