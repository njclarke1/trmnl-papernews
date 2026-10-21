# Phase 0 — make the Kindle render at native resolution

**Why first:** LaraPaper has the Kindle as "Amazon Kindle PW 6th Gen, 1024×768". The Paperwhite 11th gen panel is **1236×1648 at 300 ppi**, so every image is rescaled. That is the likely cause of the known bug where interactive refresh scales correctly but auto-refresh doesn't. Dense newspaper type is the worst case for rescaling.

**Gate:** three unattended auto-refreshes of `plugin/phase0_test.liquid`, each pixel-exact on the panel.

LaraPaper column names below are **unverified**. Every step lists the real attributes first, so adapt the commands to what they show.

---

## 1. Confirm the panel's native size

From KOReader on the Kindle, start the SSH server: *Tools → More tools → SSH server → Start*. It listens on port 2222. Then:

```bash
ssh -p 2222 root@<kindle-ip>
eips -i | head -20                              # Kindle native: xres / yres / bpp
cat /sys/class/graphics/fb0/virtual_size        # fallback
```

Expected: 1236 × 1648, or 1648 × 1236 if the framebuffer is stored landscape. Note which, because it decides the rotation in step 3.

## 2. See what LaraPaper has

```bash
ssh root@nas1
docker exec -it <larapaper-container> php artisan tinker
```
```php
App\Models\DeviceModel::query()->get()->map->only(['id','name','label','width','height','colors','bit_depth','rotation','mime_type'])->toArray();
App\Models\Device::query()->get()->map->only(['id','name','device_model_id','width','height'])->toArray();
```

If an attribute errors, run `App\Models\DeviceModel::first()->getAttributes()` to see the real column names.

## 3. Add a native model and point the Kindle at it

Copy the existing PW 6th Gen model so its colour and format settings carry over:

```php
$src = App\Models\DeviceModel::find(<pw6-id>);
$m = $src->replicate();
$m->name = 'kindle_pw11_native'; $m->label = 'Kindle PW 11th gen (1236×1648)';
$m->width = 1236; $m->height = 1648;
// if the existing model has a rotation field and the framebuffer is landscape, set it to match step 1
$m->save(); $m->id;

$d = App\Models\Device::find(<kindle-device-id>);
$d->device_model_id = $m->id;
$d->width = 1236; $d->height = 1648;     // PaperS3 lesson: the model id does not set these
$d->save();
```

Check for 16 greys: if the model has `colors` / `bit_depth`, 16 greys means `colors = 16` / `bit_depth = 4`, matching the PaperS3's `color_depth 4`.

## 4. Show the test pattern

1. In LaraPaper, create a recipe or plugin named `phase0-test` with **no polling URL**, and paste `plugin/phase0_test.liquid` as its markup.
2. Put it in the Kindle's playlist alone, or force it with your usual tinker display-cycle helper.
3. Check the rendered PNG's size on the NAS before looking at the Kindle:
   ```bash
   docker exec <larapaper-container> sh -c 'ls -t storage/app/public/images/generated | head -3'
   docker exec <larapaper-container> php -r 'print_r(getimagesize("storage/app/public/images/generated/<file>.png"));'
   ```
   It must be 1236 × 1648, or 1648 × 1236 if the device expects landscape and rotates.

On the Kindle, all three must hold:
- the frame touches all four edges
- the circle is round
- the 1 px line boxes are crisp
- all 16 grey steps are distinct

## 5. Auto-refresh

Leave it on auto-refresh for three cycles. If interactive refresh is exact but auto-refresh isn't, the scaling happens in `trmnl.koplugin`'s refresh path, not in LaraPaper. Search the plugin source for its image widget options:

```bash
grep -nE "scale|width|height|ImageWidget|RenderImage" /mnt/us/koreader/plugins/trmnl.koplugin/*.lua
```

The fix will probably be making the auto-refresh path pass the same "no scaling / native size" option the interactive path does. That's a small fork patch, alongside the configurable `server_url` fork already needed.

## 6. Also in Phase 0

- **Toast overlay:** keep "Show notifications" off, as before.
- **Full refresh:** set KOReader to do a full (flashing) refresh on every new image. Dense type ghosts badly with partial refreshes.
- **Refresh interval:** 20 minutes matches `SLOT_MINUTES`. Each refresh then shows the next paper.
