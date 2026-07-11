# JiSpr macOS beta distribution

JiSpr's first external beta is an Apple-Silicon, macOS 14+ menu-bar app shipped
in a Developer ID-signed and Apple-notarized DMG. It does not use the Mac App
Store or TestFlight.

## What is packaged

`script/package_beta.sh` builds the SwiftUI Release app, stages a relocatable
Python runtime plus the installed local-flow dependencies under
`JiSpr.app/Contents/Resources/engine`, signs every nested Mach-O before the app,
and creates `dist/JiSpr-0.1.2-arm64.dmg`.

Speech models are not included in the DMG. The configured ASR backend downloads
its model on first use and then runs locally. LM Studio remains a separate local
application. Audio, transcripts, settings, and personalization data remain on
the Mac.

## Local artifact versus friend beta

When no Developer ID Application identity is installed, the packaging script
uses an ad-hoc signature and prints a warning. That artifact is useful for local
validation but must not be sent to testers.

A distributable friend beta requires all of the following:

1. Active Apple Developer Program membership.
2. The Apple account added in Xcode Settings under Accounts.
3. A Developer ID Application certificate and private key in this Mac's
   Keychain.
4. The stable bundle identifier `com.acrobat.jispr`.
5. A notarytool credential profile stored in Keychain.

Confirm the signing identity without exposing any secret:

```bash
security find-identity -p codesigning -v
```

The result must include `Developer ID Application`. Never put an Apple ID
password, app-specific password, `.p8` key, or certificate private key in this
repository.

## One-time notarization setup

Create an app-specific password for the Apple ID, then store it through
notarytool's secure prompt. Omitting `--password` keeps it out of shell history:

```bash
xcrun notarytool store-credentials JiSprNotary \
  --apple-id YOUR_APPLE_ID \
  --team-id YOUR_TEAM_ID
```

App Store Connect API-key authentication is also supported by notarytool and is
preferable for automated CI later.

## Build the external beta

```bash
export JISPR_NOTARY_PROFILE=JiSprNotary
./script/package_beta.sh
```

The script automatically selects the installed Developer ID Application
identity. Set `JISPR_SIGNING_IDENTITY` only when the Keychain contains more than
one possible identity.

The release succeeds only after the final DMG is accepted by Apple's notary
service and the ticket is stapled and validated.

## Release validation

```bash
codesign --verify --deep --strict --verbose=2 \
  build/JiSprRelease/DerivedData/Build/Products/Release/JiSpr.app
spctl -a -vvv -t execute \
  build/JiSprRelease/DerivedData/Build/Products/Release/JiSpr.app
xcrun stapler validate dist/JiSpr-0.1.2-arm64.dmg
```

Before sharing broadly, install the DMG on a different Apple-Silicon Mac that
does not have this repository or its Python environment. Verify drag-to-
Applications installation, Gatekeeper acceptance, first-run model download,
Microphone/Accessibility/Input Monitoring prompts, Fn dictation, insertion,
Settings persistence, Launch at Login, and an update over the previous build.
