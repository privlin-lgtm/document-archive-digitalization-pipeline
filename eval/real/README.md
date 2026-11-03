# Real-scan drop-in folder

This folder is where real archive scans go for calibration. Everything in
it except this README and `TEMPLATE.json` is gitignored and dockerignored,
so client material is never committed and never baked into an image. Keep
it that way: don't `git add -f` anything from here.

## The short version

```bash
python -m eval.calibrate init     # once: creates the subfolders below
# copy scans into eval/real/images/
python -m eval.calibrate stubs    # one blank ground-truth file per new scan
# fill in each eval/real/ground_truth/<id>.json, set "status": "done"
python -m eval.calibrate run      # score them; report lands in eval/real/reports/
```

In Docker (no local Python or Tesseract needed, no database, no network):

```bash
docker compose run --rm --build eval                                   # = run
docker compose run --rm --build eval python -m eval.calibrate stubs         # any subcommand
```

`--build` makes sure the image has the current harness code. The service
bind-mounts this folder, so scans stay on your machine and reports appear
here.

## Layout

```
eval/real/
  images/          your scans: .png .jpg .jpeg .tif .tiff .bmp, one page each
  ground_truth/    one <id>.json per scan (made by `stubs`, filled in by you)
  label-pages/     <id>.png: the page as the pipeline sees it after
                   preprocessing; draw region boxes against this image
  reports/         <date>_<time>/report.md, report.json, overlays/<id>.png
```

## Filling in a ground-truth file

`stubs` writes a file like this for each scan that has none yet (it never
overwrites one). `TEMPLATE.json` here is a filled-in example.

```json
{
  "id": "parish-ledger-1872-p014",
  "image": "parish-ledger-1872-p014.png",
  "status": "todo",
  "text": "",
  "entities": [],
  "regions": null,
  "notes": "",
  "page_size": [2480, 3508]
}
```

- **status**: `"todo"` pages are listed in the report as not scored yet.
  Set `"done"` once the page is transcribed; only then is it scored.
- **text**: the whole page transcribed exactly (spelling, case,
  punctuation) as one line. Line breaks and spacing don't matter, only the
  words do. For an illegible word, write your best reading and say so in
  `notes`.
- **entities**: every entity a correct extraction should find. `type` is
  one of `person`, `date`, `location`, `amount`, and `value` uses the
  pipeline's normalized form, because scoring is exact match:
  - `person`: the name as written, spaces collapsed (`"John A. Smith"`)
  - `location`: the modern name if the place is in the historical
    gazetteer (`"Bombay"` is written `"Mumbai"`), otherwise as written,
    title-cased
  - `date`: `YYYY-MM-DD`
  - `amount`: `"<CURRENCY> <units>.<cents>"`, e.g. `"USD 128.50"`; pounds,
    shillings and pence become decimal pounds (£5 4s 3d is `"GBP 5.21"`)
- **regions** (optional): `null` means "not labeled", and the page is left
  out of the layout scores. To score layout, list every region on the page
  as `{"type": ..., "bbox": [x, y, width, height]}` in whole pixels,
  measured on `label-pages/<id>.png` (not the original scan, because deskew
  and border cropping can shift coordinates). `type` is one of
  `paragraph`, `table`, `signature`, `margin_annotation`, `stamp`. An empty
  list `[]` means the page really has no regions.
- **page_size**: written by `stubs`; the size of the label page. If
  preprocessing later changes and the size no longer matches, the report
  warns that this page's boxes need checking.
- **notes**: free text, for you. Not scored.

Labeling regions from scratch is slow. `python -m eval.calibrate stubs
--prefill-regions` starts each new stub's `regions` from what the segmenter
finds today, so you only fix types and adjust or delete boxes. Check every
box: anything you leave unchanged scores as correct, which flatters the
segmenter if you skim.

Any image viewer that shows the cursor's pixel position works for reading
box coordinates (for example GIMP, Paint.NET, or Preview's inspector).

## What `run` checks first

Before anything is scored, every `"done"` file is validated, and all the
problems are listed together: bad JSON, a missing image, empty text, an
unknown entity or region type, a malformed box, or two files sharing an id.
Nothing runs until they are fixed (exit code 2). If no page is `"done"` yet,
`run` says so and exits with code 1.

## Reading the report

`reports/<date>_<time>/report.md` has, in order:

1. **Summary**: pages scored and waiting, mean CER/WER, entity
   precision/recall/F1 over all types, and layout detection
   precision/recall plus classification accuracy.
2. **Worst pages** (up to 5, ranked by CER, then entity errors, then layout
   errors), each with the reference and predicted text, missed and
   spurious entities, and every layout mistake (missed box, extra box,
   wrong type).
3. **Entities by type**: precision/recall/F1 per type.
4. **Layout regions**: per-type precision/recall/F1 and a confusion matrix
   (labeled type against predicted type). A predicted box matches a
   labeled box when their overlap (IoU) is at least 0.5; a region only
   counts as correct when it is found *and* given the right type.
5. **All pages**: one row per page, including the scan quality score.
6. **Not scored yet**: stubs still `"todo"` and images with no file.

`overlays/<id>.png` shows each page with predicted regions in red (with
their type) and your labeled regions in green. `report.json` holds the same
numbers, plus every page's full predicted text and regions, for comparing
two runs.

## Limits for now

- One image is one page. A multi-page TIFF is read as its first page only,
  and PDFs are not accepted. Split them into one image per page for now;
  reading PDFs and multi-page TIFFs directly is a follow-up once the
  pipeline's multi-page support lands.
- The harness measures; it doesn't tune. Layout thresholds live in
  `ocr/layout.py` and are changed there, then re-measured with `run`.
- On a Linux host, the container runs as user 10001, which may not be
  allowed to write into this folder. If `run` fails with a permission
  error there, run `chmod -R a+rwX eval/real` once, or run it locally
  instead. Docker Desktop on Windows and macOS is not affected.
