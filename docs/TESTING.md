# Testing

## Automated suite

From the repository root, using the virtual environment described in the README:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The suite uses the standard-library `unittest` runner, temporary directories,
and fake network and desktop adapters. It makes no live NASA requests and does
not change the desktop or register tasks.

Coverage includes:

- Current and legacy APOD payloads, image URLs, and media-type handling.
- Duplicate entries, stale entries, changed fit, and manual refreshes.
- Video alert deduplication and manual prompts with background alerts disabled.
- Certificate verification, retry limits, DNS errors, timeouts, and HTTP errors.
- Invalid and truncated images, transparency, and download size limits.
- Wallpaper-adapter failures, restore behavior, state writes, and cache retention.
- Concurrent-update exclusion and Task Scheduler XML settings.

## Recorded checks for 1.0.1

On October 18, 2026, all 38 automated tests passed on Linux with Python 3.12,
Pillow 12.3.0, and truststore 0.10.5.

Additional checks completed:

- Application and installation Python files compile.
- Generated task XML validates against Microsoft's Task Scheduler XSD.
- The October 17 APOD image, "3D 67P", downloaded and decoded at 2628 × 2417 pixels.
- The October 18 video, "Sonified: Eagle Nebula Pillars", reached the Tk popup
  through the live NASA client. No image download or desktop change was attempted.
- The connection diagnostic produced a successful report for the video entry.
- The Tk interface rendered and its update and schedule controls were exercised
  with fake Windows adapters.

These checks do not establish Windows integration compatibility. Windows desktop
changes and Task Scheduler registration have not yet been verified in the
recorded checks for this release.

## Windows integration checklist

Run on Windows 10 or 11 under an ordinary user account:

- [ ] Install with `Install.cmd`; confirm the shortcut launches the app.
- [ ] Apply an image using **Update now** and confirm the desktop changes.
- [ ] Check **Fit**, **Fill**, and **Span** on the intended monitor setup.
- [ ] Run the `APOD Wallpaper - <SID>` task from Task Scheduler with the app
      window closed; confirm it completes without a console window.
- [ ] Confirm hourly and sign-in triggers run, including on battery power.
- [ ] Check video-day behavior with background alerts both enabled and disabled.
- [ ] Disconnect networking and confirm a failed update preserves the wallpaper.
- [ ] Restore the original wallpaper and confirm automatic updates are paused.
- [ ] Uninstall and confirm the task and shortcuts are removed.

## Connection diagnostics

`Diagnose.cmd` checks the feed and, on image days, downloads and validates the
image. It does not change the wallpaper or the automatic schedule. The report
is saved to `%LOCALAPPDATA%\APODWallpaper\diagnostics.txt`.

The rotating application log is stored in the same directory as `app.log`.
Review reports for personal paths before attaching them to an issue.
