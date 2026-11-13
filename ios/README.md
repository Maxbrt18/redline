# Redline for iOS

The athlete's notebook as a native iPhone app. It reads and writes through the coach
server's `/notebook-api/` routes; it holds no coaching logic of its own.

## Layout

| Path | What it holds |
|---|---|
| `RedlineKit/` | Swift package: everything a test can check without a simulator (API client, models, formatting, session logic). Builds for macOS too, so `swift test` runs on the host. |
| `RedlineKit/Sources/RedlineKit/Notebook/NotebookViews.swift` | the API's types, generated from `src/redline/model/notebook_views.py` by `tests/ios_contract.py`, with the payload fixtures and round-trip tests: never edited by hand. |
| `Redline/` | the SwiftUI app: screens and components. A synchronized folder: a file added here is in the target. |
| `RedlineLive/` | the widget extension: the guided strength session's Live Activity, on the lock screen and in the Dynamic Island. It shares the palette, the figures' face and `Live/StrengthLiveIntent.swift` with the app (the project's membership exceptions); its state is built and tested in `RedlineKit` (`StrengthLive.swift`). |
| `Config/` | build settings as `.xcconfig` files, the Info.plist keys the build fills, and the example of the instance's local values. |
| `scripts/` | `install-on-device`: build, sign and install on a paired iPhone. |

## Build and install

You need Xcode 26 or later and an Apple account; a free Personal Team is enough.

1. Copy `Config/Local.example.xcconfig` to `Config/Local.xcconfig` and fill in your team
   identifier, a bundle prefix that you own, and your coach server's host. Git ignores
   this file: no team, identifier or host is ever committed.
2. Open `Redline.xcodeproj` and run the `Redline` scheme, or install from the command line:

   ```bash
   ios/scripts/install-on-device "<device name>"
   ```

   `xcrun devicectl list devices` lists the paired devices. Over Wi-Fi, the iPhone must
   be unlocked and on the same network as the Mac.

With a free Personal Team, Apple's documentation states that the provisioning profile
lasts 7 days; when it expires, the app stops opening until it is installed again. Your
data is kept on reinstall.

## Checks

```bash
cd ios/RedlineKit && swift test
```

```bash
xcodebuild -project ios/Redline.xcodeproj -scheme Redline \
    -destination 'generic/platform=iOS Simulator' CODE_SIGNING_ALLOWED=NO build
```

Both run in CI, in the `ios` job of `.github/workflows/code-checks.yml`.

## Language

The athlete reads French: the app's words stay inline, in French, in the SwiftUI views,
and the project's development region is `fr`. Code, comments and this file are English,
as everywhere in the engine (`AGENTS.md`).
