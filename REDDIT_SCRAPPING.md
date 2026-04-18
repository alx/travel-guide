**Tier 1 — highest NER yield, structured itinerary shape**

These are where day-by-day trip reports with named places are the *norm*, not the exception:

- **r/JapanTravel** — the gold standard. Mandatory "Trip Report" flair, most posts are structured "Day 1: Shinjuku → meiji shrine → Omoide Yokocho" format. High density of specific restaurants, ryokan names, station names. Your Photon geocoder will love it.
- **r/ThailandTourism** and **r/Thailand** — trip reports reference specific beaches, sois, temples, night markets. Good for your existing SEA maps.
- **r/VietnamTravel** — similar structure, often segments by city (Hanoi/Hue/Hoi An/Saigon).
- **r/travel** — the mothership. Filter by flair=`Trip Report` + sort by top-of-all-time. These are often 3000+ word posts with per-day breakdowns.
- **r/solotravel** — "Trip Report" flair, 2M+ members, tends toward longer narrative with named hostels/cafes.
- **r/backpacking** and **r/Shoestring** — route-heavy (e.g. "Banana Pancake Trail Day-by-Day"), good for multi-country itineraries.

**Tier 2 — route-shaped content, great for linear maps**

Your fishing-boat and Phuket-roadtrip maps show you handle linear/sequenced data well. These subs are linear by nature:

- **r/roadtrip** and **r/Roadtrippers** — US & international routes, often with mile markers and stops.
- **r/overlanding** and **r/vandwellers** — multi-week routes, GPS-minded audience, often post actual tracks.
- **r/CampingandHiking**, **r/Ultralight**, **r/WildernessBackpacking** — trail reports with trailheads, shelters, water sources. If you ever branch into hiking maps, this is a goldmine.
- **r/bicycletouring** and **r/bikepacking** — cyclists document routes obsessively, often with Komoot/Strava links embedded.
- **r/PacificCrestTrail**, **r/AppalachianTrail**, **r/CaminoDeSantiago** — ready-made linear corridors with shelter/town sequences.

**Tier 3 — city-level depth (for neighborhood maps like your Lamai/Hoi An ones)**

- **r/AskNYC, r/AskLosAngeles, r/london, r/paris, r/tokyo, r/bangkok, r/chiangmai, r/HoChiMinhCity** — hyperlocal "best pho in District 1" threads. Lower structure but huge place-name density per comment. Good for generating themed maps (coffee, street food, viewpoints) rather than itineraries.
- **r/AskCulinary** + city subs — food-focused pins, which travelers love.

**Tier 4 — niche but exceptionally high signal-to-noise**

- **r/digitalnomad** — city reviews with specific co-working spaces, cafes, neighborhoods. Matches your own demographic.
- **r/onebag** — trip reports as a side effect of gear discussion, named hotels/hostels.
- **r/IndiaTravel, r/MexicoTravel, r/ItalyTravel, r/spain, r/portugal, r/greece** — follow the same pattern as JapanTravel at lower volume.

---

**Practical suggestions for your pipeline**

Since you're already using Reddit → spaCy → Photon, a few adjustments would dramatically increase your yield:

1. **Target flair, not subreddits.** Across r/travel, r/JapanTravel, r/solotravel, r/ThailandTourism, the `Trip Report` flair is the signal. Pushshift is gone, but the Reddit API's `search?q=flair_name:"Trip Report"` still works per-sub. One pipeline across 20 subs filtered by flair beats scraping whole subs.

2. **Day-N headers are a parsing goldmine.** Posts with `Day 1:`, `Day 2:` or `# Day 1` markdown headers give you free itinerary ordering — you can emit sequenced GeoJSON features with a `day` property instead of an unordered point cloud. Worth a regex pre-pass before NER so you preserve order.

3. **Comments > body for some subs.** On r/AskNYC-style subs, the post is a question and the *comments* are the place-dense content. Different parsing path (threaded, vote-weighted) but very high yield per thread.

4. **Geocoding disambiguation is your bottleneck at global scale.** "Temple Street" exists in Hong Kong, London, and Singapore. Since your schema already has `coord_source`, consider adding a `context_country` hint extracted from the post's flair or title — feeding it to Nominatim as a `countrycodes=` param cuts false positives hard.

5. **Language note.** r/travel and r/solotravel skew English-language even for non-English destinations, so your French-speaking audience gets English trip reports of French/Spanish/Japanese places. If you want Francophone source content, **r/voyage** and **r/france** (travel flair) are your equivalents — smaller but native-voice.

