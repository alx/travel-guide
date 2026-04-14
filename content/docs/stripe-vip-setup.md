---
title: "VIP Stripe Setup Guide"
draft: true
---

# VIP POI — Stripe Setup & Testing Guide

This guide walks through configuring Stripe payments to unlock VIP POIs on the travel maps.

## Overview

VIP POIs are stored in the GeoJSON with `"premium": true`. When a visitor clicks the 🔒 VIP filter or a locked card, a paywall modal appears. After payment, their session should be marked as paid and the VIP POIs unlocked.

## Step 1 — Create a Stripe Account

1. Go to https://dashboard.stripe.com/register
2. Complete business verification (can use "Individual" for personal projects)
3. Switch to **Test mode** (toggle in top-left of dashboard)

## Step 2 — Create a Product & Price

1. In Stripe Dashboard → **Products** → **Add product**
2. Name: `Travel Maps VIP Access`
3. Pricing model: **Recurring** → Monthly → **€4.99**
4. Click **Save product**
5. Copy the **Price ID** (starts with `price_...`) — you'll need this

## Step 3 — Create a Stripe Payment Link

1. In Stripe Dashboard → **Payment Links** → **New**
2. Select your VIP Access product
3. Set success URL to: `https://maps.girard-davila.net/?vip=unlocked`
4. Copy the Payment Link URL (e.g. `https://buy.stripe.com/...`)

## Step 4 — Wire the Payment Link into the Map

In `layouts/koh-samui/list.html` and `layouts/hoi-an/list.html`, find the paywall modal and update the "Unlock Now" button href:

```html
<a href="https://buy.stripe.com/YOUR_LINK" style="...">Unlock Now →</a>
```

Also update the VIP button on the homepage (`layouts/index.html`).

## Step 5 — Handle the Success Redirect

After payment, Stripe redirects to `?vip=unlocked`. Add this JS to the map pages to detect it and auto-unlock:

```js
// Check if returning from Stripe payment
if (new URLSearchParams(window.location.search).get('vip') === 'unlocked') {
  localStorage.setItem('vip_unlocked', '1');
  // Remove param from URL cleanly
  window.history.replaceState({}, '', window.location.pathname);
}

// Check VIP status on load
const isVIP = localStorage.getItem('vip_unlocked') === '1';
```

Then in `renderSidebar()`, check `isVIP` before showing the lock overlay:
```js
if (p.premium && !isVIP) {
  // show locked card
} else {
  // show full card
}
```

And in `updateMarkers()`:
```js
if (p.premium && !isVIP) {
  marker.on('click', () => document.getElementById('paywall-modal').style.display='flex');
} else {
  marker.on('click', () => focusPOI(f));
}
```

## Step 6 — Test the Flow

### Test mode (no real money)
1. Use test Payment Link (Stripe test mode must be ON)
2. Visit your map page
3. Click the 🔒 VIP filter → paywall modal should appear
4. Click "Unlock Now →" → redirects to Stripe Checkout
5. Use test card: `4242 4242 4242 4242` / any future date / any CVC
6. After payment: redirected to `?vip=unlocked`
7. VIP POIs should now be visible (full name, notes, GPS)
8. Reload the page → VIP should still be unlocked (localStorage)

### Verify in browser
```
localStorage.getItem('vip_unlocked')  // should return "1"
```

### Verify in Stripe Dashboard
- Go to **Payments** in test mode
- Should see a successful ฿4.99 payment
- Click it → confirm product is "Travel Maps VIP Access"

## Step 7 — Go Live

1. Switch Stripe to **Live mode**
2. Create same product/price/payment link in live mode
3. Update the payment link URL in the layouts
4. Done — real payments will now unlock VIP access

## Notes

- `localStorage` persists across sessions on the same device/browser
- For multi-device support, a backend (Firebase/Firestore) would be needed to store VIP status per user
- Current implementation: simple, client-side, no backend required
