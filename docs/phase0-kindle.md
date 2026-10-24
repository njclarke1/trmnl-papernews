# Phase 0 — the Kindle (passed 24 Oct 2026)

## Device as found (Nick's handover)

- Kindle Paperwhite 11th gen, 6.8", 300 ppi, 16 greys. Jailbroken (Véra); KOReader + `usetrmnl/trmnl-koreader`.
- Permanent USB-C power; Keep alive on; Auto suspend off. E-ink refresh type **Full** (UI mode left artefacts); status notifications hidden.
- LaraPaper `http://192.168.68.111:4567`, device ID 3 "Poppy's Kindle PW11", model preset "Amazon Kindle PW Signature 11th Gen" (model_id 54).
- Was set up landscape: width 1648, height 1236, rotate 90 → delivered PNG 1236 × 1648 (checked via naturalWidth/Height). Refresh 900 s, server-driven.
- Manual regenerate: `php artisan trmnl:screen:generate 3` (container shell is `/bin/sh`).

**The native framebuffer is 1236 × 1648 portrait, so the preset needs no scaling. papernews is designed at exactly that size.**

## Decision: portrait — needs a custom device model

**First test (24 Oct):** the test pattern came out magnified about 2.06×, showing only the top-left 800 × 600 or so of the page.

The resolution is not the problem: the preset's 1648 × 1236 is correct. The problem is the **CSS canvas** the page is laid out on. TRMNL's own definition of this preset (`epaper-pipeline-php` `models.json`) is:
- css class `screen--amazon_kindle_paperwhite_signature_11th_gen`
- css variables `--screen-w: 800px`, `--screen-h: 600px`, `--pixel-ratio: 2.06`
- rotation 90 (laid out landscape, then rotated onto the portrait panel)

So the framework lays recipes out on an 800 × 600 CSS screen and scales it 2.06× to fill the panel. That suits TRMNL's 800-px layouts. papernews is a native 1236 × 1648 portrait page (1 CSS px = 1 panel pixel), so only its top-left 800 × 600 shows, magnified.

**Diagnostic result (24 Oct, `plugin/diag.liquid`):** once device 3 was on a duplicated model set to 1236 × 1648 with rotation 0:
- the browser window was exactly right: 1236 × 1648 at devicePixelRatio 1;
- the TRMNL framework's wrapper still had class `screen--amazon_kindle_paperwhite_signature_11th_gen` with `--screen-w: 800px`, `--screen-h: 600px`, `--pixel-ratio: 2.06`, and `transform: matrix(2.06, 0, 0, 2.06, 0, 0)` on an 800 × 600 `.screen`.

**Settled fix (two parts):**
1. **Device model:** a manual copy of the PW11 preset at 1236 × 1648, rotation 0 (scale factor 1). This sets the window size and stops the 90° rotation. *Done.*
2. **Templates:** `full.liquid`, the test pattern and the diagnostic reset `.screen` (`transform: none`, 1236 × 1648) themselves (commit 493c33c). This works whatever the framework's CSS class or variables say, so clearing the CSS name and variables on the model is now optional tidying. The render harness renders inside the same wrapper and checks the page box is 0, 0, 1236, 1648.

**Test pattern after the fix (24 Oct): geometry passes.** The frame reaches all four edges, the circle is round and the 1-px line boxes are crisp. **The grey ramp fails:** only 8 levels show (00, 11, 33, 55, 99, cc, ee, ff); 22/44, 66/77, 88/aa and bb/dd merge into their neighbours.
- Cause: the `gray-16` palette has `colors: null`, so the image pipeline (`epaper-pipeline-php` `ImageStage::quantize`) runs an *adaptive* `quantizeImage(16, GRAY, dither)`. It picks the 16 greys that best fit each image's histogram, with anti-aliased text taking most of the slots, rather than the panel's 16 fixed levels.
- Fix: in **Device Palettes** (`/device-palettes`), create `kindle-gray16-fixed` with grays 16, framework class `screen--4bit` and colours `#000000 #111111 #222222 #333333 #444444 #555555 #666666 #777777 #888888 #999999 #aaaaaa #bbbbbb #cccccc #dddddd #eeeeee #ffffff`. Set it as the palette on the portrait model. With an explicit colour list, the pipeline remaps to exactly those levels (`applyColormap`) before quantising.

**Phase 0 passed (24 Oct, 07:29)** with the fixed palette on the portrait model: frame, corners, round circle, crisp 1-px lines and 16 distinct grey steps.

Device refresh, or the playlist's refresh time: 180 s, the same as `SLOT_MINUTES=3`.

Rotation is set per device. Anything else on device 3's playlist would also need a 1236 × 1648 portrait layout.

## LaraPaper plugin settings

- Polling URL `http://192.168.68.111:3461/edition`; markup `plugin/full.liquid`.
- **Refresh interval 1 minute.** On each device fetch, LaraPaper re-polls only stale data (`RunDeviceDisplayCycle::renderSinglePlugin`). A 1-minute interval makes every 3-minute fetch poll the sidecar and render the next paper.
- The sidecar's default `ROTATION_MODE=poll` moves on one paper per fetch. Fetches less than ⅔ of a slot apart (previews, manual renders) repeat the current paper, so nothing is skipped as the device timer drifts.

## Still worth a two-minute check

Paste `plugin/phase0_test.liquid` as a temporary plugin at rotate 0. If the 1-px hairlines, the 22–30 px type samples and the 16-step grey ramp are sharp and distinct, the image is mapped 1:1 with no rescale.

Then render `/demo/guardian.json` with `IMAGE_MODE=halftone` and then `clean`, and choose the photo treatment on the panel itself. The handover advises against fine dithering; the halftone is a coarse dot screen, so judge it on the device.
