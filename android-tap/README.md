# Resolve Tap — presence-only Android companion

This native app can detect compatible ISO-DEP contactless presence while it is in the foreground. A real contactless debit card may work, but compatibility must be tested on the actual phone/card. Detection does **not** identify a card, prove ownership, or authenticate a banking customer. Any compatible NFC object could trigger this demo.

No PAN, expiry, UID, NDEF payload, EMV application, APDU, or banking record is requested by the app. It inspects only `Tag.getTechList()` for ISO-DEP, with Android reader mode and `FLAG_READER_SKIP_NDEF_CHECK`. No `connect`, `transceive`, or `getId` calls. The companion has no Internet permission and forwards no NFC/card data. After explicit confirmation, it opens the system browser with the *preselected* short-lived demo pairing link. Only the browser talks to the local web app. No payment or refund is authorized by tapping.

## Build and install

Requirements: NFC-enabled Android phone (Android 8/API 26+), NFC enabled, contactless card, USB cable, Android Studio with JDK 17, Android SDK Platform 35 and Build Tools 35.0.0. AGP 8.13.0 and official Gradle 8.13 wrapper are pinned; the Gradle distribution SHA-256 is included. The Mac used for implementation did not have the Android toolchain installed: **the APK has not been compiled, installed, or hardware-tested here**.

1. Open this `android-tap` folder as an Android Studio project. Allow Gradle sync to fetch official build dependencies. Use JDK 17 and install SDK 35 if prompted.
2. Connect your phone. Enable developer options/USB debugging yourself, and authorize your own computer on the phone.
3. Run the `app` configuration on the connected phone. Alternatively, after configuring the SDK, run `./gradlew assembleDebug` then install `app/build/outputs/apk/debug/app-debug.apk` using Android Studio or `adb install`.
4. Do not install APKs from unrelated sources. This repository's companion has only the NFC permission.

## Recommended local connection: USB

Keep the existing FastAPI and Next.js servers bound to `127.0.0.1` on your Mac. Do not expose this unauthenticated demo workspace to the Internet.

With the phone connected and authorized, run Android Studio's terminal:

```bash
adb devices
adb reverse tcp:3000 tcp:3000
```

If `adb` is not on PATH on macOS, Android Studio normally installs it at `$HOME/Library/Android/sdk/platform-tools/adb`. Run `adb -s YOUR_DEVICE_SERIAL reverse tcp:3000 tcp:3000` when more than one device is connected. USB reverse makes the phone's `http://127.0.0.1:3000` reach this Mac's frontend. Next.js proxies `/api` to the Mac backend; forwarding port 8000 is unnecessary. Verify the page opens in the phone's browser **before** attempting NFC.

A same-Wi-Fi setup requires explicitly binding the frontend to a private network interface and adapting firewall settings. This implementation does not change firewall settings or expose the server automatically; USB forwarding avoids that extra setup. Never use a public tunnel for this seeded-account prototype.

## End-to-end rehearsal

1. On the desktop, create **Electronics → Out of stock** in **Order rescue**. Wait for its customer-choice state. Record its checkout ID.
2. Open **Tap to resolve**. Select that checkout and **Create pairing link**. The link expires in five minutes, is single-use, and contains a random capability in the URL fragment. Keep it private. It carries no card information. Copy it to your phone using your own chosen method; the app never reads clipboard contents automatically.
3. Open **Resolve Tap Demo** on the phone, paste the pairing link, and press **Arm NFC reader**. Enable NFC in Android settings if necessary. Keep the phone unlocked and the app in front.
4. Hold your own contactless card at the phone's NFC antenna. Remove other cards from the area. If ISO-DEP is detected, the app shows **Compatible NFC presence detected**. It does not send banking commands or confirm this is a bank card.
5. Press **Continue to demo checkout**. In the phone browser, review and press **Confirm demo handoff**. This records `android_nfc`, explicitly self-reported and not hardware-attested.
6. The desktop pairing card changes to **REDEEMED**. Press **Open paired checkout**. Confirm it opens the exact selected checkout, not an inferred card-linked bank transaction.
7. Continue the existing consented alternative, order rescue, or policy-controlled refund workflow. Tap itself does not alter checkout, order, refund, payment, or workflow state. Background recovery that was already underway continues independently.
8. Try the pairing link again: the backend must refuse it. Try cancellation and a five-minute expiry: neither link may redeem.

## Fallback and limits

- No NFC setting/hardware: use the web **Simulate tap (no hardware)** fallback. It records `simulation`, never a claim of physical detection.
- Non-contactless/incompatible card, phone antenna position, thick case, NFC disabled: explain that hardware detection is unverified and use the existing scan/transaction flow. Do not claim successful physical NFC from software tests.
- Switching away from the companion stops reader mode; arm it again on return.
- Pairing status and cancellation require a separate owner secret. Raw secrets are never stored in SQLite; only SHA-256 digests are retained. Redemption is transactional, bounded by expiry, and one-time. Tokens are removed from the browser address bar after loading and kept only in component memory. Refreshing that phone page requires reopening a still-unused link.
- `android_nfc` is a client report, not cryptographic hardware attestation. These routes intentionally cannot authorize money or prove identity. Existing seeded demo account is not production authentication.
- Sarvam remains paused. No NFC, speech, or model provider is invoked by the companion or tap endpoints. Paytm remains mocked.

Official references: [Android reader mode](https://developer.android.com/reference/android/nfc/NfcAdapter), [Web NFC restrictions](https://developer.chrome.com/docs/capabilities/nfc), [Android Gradle Plugin compatibility](https://developer.android.com/build/releases/agp-8-13-0-release-notes).
