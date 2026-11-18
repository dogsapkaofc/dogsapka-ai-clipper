# DOGSAPKA – iPhone távirányító

SwiftUI app (iOS 16+), amivel a számítógépen futó DOGSAPKA AI CLIPPER vezérelhető a helyi hálózaton:
élő állapot és pontszámok, figyelés indítása/leállítása, klipek lejátszása és feltöltése, feldolgozási sor.

- A projekt fájl generált: `project.yml` → `xcodegen generate` (a `.xcodeproj` nincs a repóban).
- Minden push után a GitHub Mac-gépe lefordítja és szimulátoron teszteli (**Actions → iOS remote app**).
- TestFlight-ra a GitHub tölti fel, ha be vannak állítva az Apple kulcsok (lent).

## Használat

1. Számítógépen: **Beállítások → Távirányító (iPhone)** → *Távirányító bekapcsolása* → **Beállítások mentése**.
   A Windows egyszer rákérdez a tűzfalra: engedélyezd **magánhálózaton**.
2. **iPhone párosítása** → megjelenik egy QR-kód és egy 6 jegyű kód (10 percig érvényes).
3. iPhone-on: DOGSAPKA app → **QR-kód beolvasása** (vagy cím + kód kézzel). Az app engedélyt kér a
   *helyi hálózat* eléréséhez – engedélyezd.
4. A párosított telefonok a számítógépen bármikor letilthatók.

Otthonon kívülről: használj VPN-t (pl. Tailscale, mindkét eszközön) és a VPN-címmel párosíts.
A portot **soha ne** nyisd ki a routeren az internet felé.

## Telepítés a telefonra Windowsról (Sideloadly) – nem kell fejlesztői fiók

A GitHub minden buildnél készít egy **aláíratlan `.ipa`** fájlt; ezt a Sideloadly a saját Apple ID-ddal
aláírja és a telefonra teszi.

1. **Az .ipa letöltése**: GitHub → **Releases** → a legújabb `ios-v…` kiadás →
   `DOGSAPKA_Remote_…_unsigned.ipa`
   (vagy **Actions → iOS remote app** → a legutóbbi sikeres futás → *Artifacts* → `DOGSAPKA-iOS-unsigned-ipa`).
2. **iTunes** (az Apple oldaláról, *nem* a Microsoft Store-os változat) – a Sideloadly ezzel látja a telefont:
   https://www.apple.com/itunes/download/win64
3. **Sideloadly**: https://sideloadly.io → telepítsd és indítsd el.
4. Telefon USB-n a gépre → a telefonon *Megbízol ebben a számítógépben?* → **Megbízom**.
5. Sideloadly: húzd be az `.ipa`-t → *Apple ID* mezőbe a saját Apple ID-d → **Start** → add meg az Apple ID
   jelszavát (azt csak az Apple kapja meg, a Sideloadly aláíráshoz használja).
6. iPhone-on: **Beállítások → Általános → VPN és eszközkezelés** → a saját Apple ID-d alatt
   → **Megbízom**. iOS 16+: **Beállítások → Adatvédelem és biztonság → Fejlesztői mód** → be (újraindul).
7. Nyisd meg a DOGSAPKA appot.

**Ingyenes Apple ID-vel az app 7 napig fut**, utána a Sideloadly-ben ugyanígy újra kell telepíteni
(az app adatai, a párosítás megmaradnak). Fizetős fejlesztői fiókkal (99 USD/év) ez 1 év.

## Telepítés TestFlighttal (opcionális) – egyszeri beállítás

Ehhez Apple fejlesztői tagság kell (**évi 99 USD**, a saját Apple ID-ddal).

1. **Apple Developer Program**: https://developer.apple.com/programs/enroll/ → egyéni (Individual) tagság.
2. **App ID**: https://developer.apple.com/account/resources/identifiers/list → **+** → *App IDs* → *App* →
   Bundle ID (Explicit): `com.dogsapka.remote`, leírás: *DOGSAPKA Remote*.
3. **App rekord**: https://appstoreconnect.apple.com/apps → **+** → *New App* → iOS, név pl. *DOGSAPKA Remote*
   (egyedinek kell lennie az App Store-ban), nyelv: Magyar, Bundle ID: `com.dogsapka.remote`, SKU: bármi.
4. **API kulcs**: https://appstoreconnect.apple.com/access/integrations/api → *Team Keys* → **+** →
   név: *GitHub*, hozzáférés: **Admin** → *Generate*. Töltsd le a `.p8` fájlt (**csak egyszer lehet!**),
   és jegyezd fel a **Key ID**-t és fent az **Issuer ID**-t.
5. **Team ID**: https://developer.apple.com/account → *Membership details* → *Team ID*.
6. **GitHub titkok**: a repóban **Settings → Secrets and variables → Actions → New repository secret**:

   | Név | Érték |
   |---|---|
   | `APPLE_TEAM_ID` | a Team ID (10 karakter) |
   | `ASC_KEY_ID` | a Key ID |
   | `ASC_ISSUER_ID` | az Issuer ID |
   | `ASC_PRIVATE_KEY` | a `.p8` fájl teljes tartalma (`-----BEGIN PRIVATE KEY-----`-tól a végéig) |

   A titkok nyilvános repóban is titkosak maradnak, senki nem látja őket.
7. **Feltöltés**: GitHub → **Actions → iOS remote app → Run workflow** → pipáld be *Upload to TestFlight* → Run.
   (Vagy egy `ios-v0.3.0` formájú tag.) Kb. 15 perc, utána az Apple 5–30 percig feldolgozza.
8. **Telepítés**: App Store Connect → az app → **TestFlight** → *Internal Testing* → csoport → add hozzá magad
   (és a barátodat – ehhez előbb *Users and Access*-ben fel kell venni felhasználónak). A telefonon a
   **TestFlight** appban megjelenik a DOGSAPKA → *Install*.

A TestFlight-buildek 90 napig érvényesek; új build feltöltésével frissül az app.
