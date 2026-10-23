# Phase 0 — the Kindle (results, 23 Oct 2026)

## Device as found (Nick's handover)

- Kindle Paperwhite 11th gen, 6.8", 300 ppi, 16 greys. Jailbroken (Véra); KOReader + `usetrmnl/trmnl-koreader`.
- Permanent USB-C power; Keep alive on; Auto suspend off. E-ink refresh type **Full** (UI mode left artefacts); status notifications hidden.
- LaraPaper `http://192.168.68.111:4567`, device ID 3 "Poppy's Kindle PW11", model preset "Amazon Kindle PW Signature 11th Gen" (model_id 54).
- Was set up landscape: width 1648, height 1236, rotate 90 → delivered PNG 1236 × 1648 (checked via naturalWidth/Height). Refresh 900 s, server-driven.
- Manual regenerate: `php artisan trmnl:screen:generate 3` (container shell is `/bin/sh`).

**The native framebuffer is 1236 × 1648 portrait, so the preset needs no scaling. papernews is designed at exactly that size.**

## Decision: portrait

The template (`plugin/full.liquid`) is a 1236 × 1648 portrait page. Set device 3 to:

| Field | Value |
|---|---|
| width | 1236 |
| height | 1648 |
| rotate | 0 |
| refresh (device, or the playlist's refresh time) | 180 s, the same as `SLOT_MINUTES=3` |

Then check the delivered PNG is still 1236 × 1648 and the right way up, and mount the Kindle upright (USB-C at the bottom).

Rotation is set per device, not per plugin. Anything else on device 3's playlist would also need a portrait layout.

## LaraPaper plugin settings

- Polling URL `http://192.168.68.111:3461/edition`; markup `plugin/full.liquid`.
- **Refresh interval 1 minute.** On each device fetch, LaraPaper re-polls only stale data (`RunDeviceDisplayCycle::renderSinglePlugin`). A 1-minute interval makes every 3-minute fetch poll the sidecar and render the next paper.
- The sidecar's default `ROTATION_MODE=poll` moves on one paper per fetch. Fetches less than ⅔ of a slot apart (previews, manual renders) repeat the current paper, so nothing is skipped as the device timer drifts.

## Still worth a two-minute check

Paste `plugin/phase0_test.liquid` as a temporary plugin at rotate 0. If the 1-px hairlines, the 22–30 px type samples and the 16-step grey ramp are sharp and distinct, the image is mapped 1:1 with no rescale.

Then render `/demo/guardian.json` with `IMAGE_MODE=halftone` and then `clean`, and choose the photo treatment on the panel itself. The handover advises against fine dithering; the halftone is a coarse dot screen, so judge it on the device.
