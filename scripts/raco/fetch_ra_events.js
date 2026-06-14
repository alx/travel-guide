// fetch_ra_events.js

async function fetchEvents(pageNumber, areaId) {
  const url = 'https://ra.co/graphql';
  
  // Your exact payload, but making 'page' and 'areas.eq' dynamic variables
  const graphqlPayload = {
    "operationName": "GET_EVENT_LISTINGS",
    "variables": {
      "filters": {
        "areas": { "eq": areaId },
        "listingDate": { "gte": "2026-06-14" }
      },
      "filterOptions": {
        "genre": true,
        "eventType": true
      },
      "pageSize": 20,
      "page": pageNumber,
      "sort": {
        "listingDate": { "order": "ASCENDING" },
        "score": { "order": "DESCENDING" },
        "titleKeyword": { "order": "ASCENDING" }
      }
    },
    // The exact query and fragment you captured
    "query": `query GET_EVENT_LISTINGS($filters: FilterInputDtoInput, $filterOptions: FilterOptionsInputDtoInput, $page: Int, $pageSize: Int, $sort: SortInputDtoInput) {
      eventListings(
        filters: $filters
        filterOptions: $filterOptions
        pageSize: $pageSize
        page: $page
        sort: $sort
      ) {
        data {
          id
          listingDate
          event {
            ...eventListingsFields
            __typename
          }
          __typename
        }
        totalResults
        __typename
      }
    }

    fragment eventListingsFields on Event {
      id
      date
      startTime
      endTime
      title
      contentUrl
      venue {
        id
        name
        __typename
      }
      artists {
        id
        name
        __typename
      }
      __typename
    }`
  };

  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Origin': 'https://ra.co',
        'Referer': `https://ra.co/events/th/kohsamui` // Good practice to match referer
      },
      body: JSON.stringify(graphqlPayload)
    });

    if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

    const jsonResponse = await response.json();
    const events = jsonResponse.data.eventListings.data;
    const totalResults = jsonResponse.data.eventListings.totalResults;
    
    console.log(`\n📄 --- PAGE ${pageNumber} ---`);
    console.log(`🎉 Found ${events.length} events on this page (Total available: ${totalResults})`);
    
    events.forEach(item => {
        const event = item.event;
        const title = event.title || 'Unnamed Event';
        const date = event.date ? event.date.split('T')[0] : 'Unknown Date';
        const venue = event.venue ? event.venue.name : 'TBA';
        
        console.log(`[${date}] ${title} @ ${venue}`);
    });

  } catch (error) {
    console.error("Error fetching data:", error);
  }
}

// Fetch Page 1 and Page 2 for Area 453 (Koh Samui)
(async () => {
  await fetchEvents(1, 453);
  await fetchEvents(2, 453);
})();
