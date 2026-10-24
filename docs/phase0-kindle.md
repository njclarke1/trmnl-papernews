# Phase 0 — the Kindle (results, 23 Oct 2026)

## Device as found (Nick's handover)

- Kindle Paperwhite 11th gen, 6.8", 300 ppi, 16 greys. Jailbroken (Véra); KOReader + `usetrmnl/trmnl-koreader`.
- Permanent USB-C power; Keep alive on; Auto suspend off. E-ink refresh type **Full** (UI mode left artefacts); status notifications hidden.
- LaraPaper `http://192.168.68.111:4567`, device ID 3 "Poppy's Kindle PW11", model preset "Amazon Kindle PW Signature 11th Gen" (model_id 54).
- Was set up landscape: width 1648, height 1236, rotate 90 → delivered PNG 1236 × 1648 (checked via naturalWidth/Height). Refresh 900 s, server-driven.
- Manual regenerate: `php artisan trmnl:screen:generate 3` (container shell is `/bin/sh`).

**The native framebuffer is 1236 × 1648 portrait, so the preset needs no scaling. papernews is designed at exactly that size.**

## Decision: portrait — needs a custom device model

**First test (24 Oct):** the test pattern came out magnified about 2.06×, showing only the top-left 800 × 600 or so of the page, on a landscape canvas.
- Cause: when a device has a model assigned, LaraPaper renders with the **model's** width, height, rotation and scale. The device's own width, height and rotate fields are only a fallback (`ImageGenerationService::getImageSettings`).
- The PW Signature 11th Gen preset is 1648 × 1236, rotation 90, `scale_factor` 2.06. That suits TRMNL's 800-px layouts; papernews is a native 1236 × 1648 page.

**Fix:** go to Device Models (`/device-models`), duplicate "Amazon Kindle PW Signature 11th Gen" (the copy is `source: manual`, so model syncs leave it alone), and set:

| Field | Value |
|---|---|
| label | Kindle PW11 portrait (native) |
| width × height | 1236 × 1648 |
| scale factor | 1 |
| rotation | 0 |
| colours / bit depth / type | 16 / 4 / PNG (unchanged) |

Then assign device 3 to the new model and regenerate (`php artisan trmnl:screen:generate 3`). Check that the frame touches all four edges, the circle is round, the line boxes are crisp and all 16 greys are distinct. Mount the Kindle upright, with USB-C at the bottom.

Device refresh, or the playlist's refresh time: 180 s, the same as `SLOT_MINUTES=3`.

Rotation is set per device. Anything else on device 3's playlist would also need a 1236 × 1648 portrait layout.

## LaraPaper plugin settings

- Polling URL `http://192.168.68.111:3461/edition`; markup `plugin/full.liquid`.
- **Refresh interval 1 minute.** On each device fetch, LaraPaper re-polls only stale data (`RunDeviceDisplayCycle::renderSinglePlugin`). A 1-minute interval makes every 3-minute fetch poll the sidecar and render the next paper.
- The sidecar's default `ROTATION_MODE=poll` moves on one paper per fetch. Fetches less than ⅔ of a slot apart (previews, manual renders) repeat the current paper, so nothing is skipped as the device timer drifts.

## Still worth a two-minute check

Paste `plugin/phase0_test.liquid` as a temporary plugin at rotate 0. If the 1-px hairlines, the 22–30 px type samples and the 16-step grey ramp are sharp and distinct, the image is mapped 1:1 with no rescale.

Then render `/demo/guardian.json` with `IMAGE_MODE=halftone` and then `clean`, and choose the photo treatment on the panel itself. The handover advises against fine dithering; the halftone is a coarse dot screen, so judge it on the device.
