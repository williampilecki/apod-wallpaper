# 1.0.1 — October 18, 2026

- Use native system certificate verification through truststore, including the
  Windows certificate-chain engine. HTTPS and hostname validation remain required.
- Request one current APOD instead of the default 25 entries.
- Allow 30 seconds per socket operation and one retry for temporary connection
  or server failures. No retry loops for certificate rejection, HTTP access denial,
  or rate limiting.
- Show the failed host and actual network error in the interface and log.
- Add Diagnose.cmd for a connection report that does not change the wallpaper.
- A manual Update now on a video entry always offers the video link, even if
  that day's background notification was already shown.
- Show the installed version in the window title to distinguish an old process
  from the updated app.

