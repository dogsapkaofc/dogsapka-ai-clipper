"""Minimal translation layer (English / Hungarian)."""
from __future__ import annotations

_LANG = "en"

STRINGS: dict[str, dict[str, str]] = {
    # navigation
    "nav.dashboard": {"en": "Dashboard", "hu": "Irányítópult"},
    "nav.streamers": {"en": "Streamers", "hu": "Streamerek"},
    "nav.live": {"en": "Live Monitor", "hu": "Élő figyelés"},
    "nav.library": {"en": "Clip Library", "hu": "Klipkönyvtár"},
    "nav.editor": {"en": "AI Editor", "hu": "AI szerkesztő"},
    "nav.queue": {"en": "Processing", "hu": "Feldolgozás"},
    "nav.settings": {"en": "Settings", "hu": "Beállítások"},
    # dashboard
    "dash.monitored": {"en": "Monitored streamers", "hu": "Figyelt streamerek"},
    "dash.live": {"en": "Live now", "hu": "Most élőben"},
    "dash.recording": {"en": "Active recordings", "hu": "Aktív felvételek"},
    "dash.clips_total": {"en": "Total clips", "hu": "Összes klip"},
    "dash.clips_today": {"en": "Clips today", "hu": "Mai klipek"},
    "dash.ai_status": {"en": "AI processing", "hu": "AI feldolgozás"},
    "dash.disk": {"en": "Free disk space", "hu": "Szabad lemezterület"},
    "dash.gpu": {"en": "GPU", "hu": "GPU"},
    "dash.queue": {"en": "Processing queue", "hu": "Feldolgozási sor"},
    "dash.activity": {"en": "Activity feed", "hu": "Események"},
    "dash.idle": {"en": "Idle", "hu": "Tétlen"},
    "dash.busy": {"en": "Working", "hu": "Dolgozik"},
    "dash.simulate": {"en": "Run simulation", "hu": "Szimuláció indítása"},
    # system tray
    "tray.open": {"en": "Open DOGSAPKA AI CLIPPER", "hu": "DOGSAPKA AI CLIPPER megnyitása"},
    "tray.quit": {"en": "Close", "hu": "Bezárás"},
    "tray.hidden": {"en": "Still running in the background. Right-click the tray icon and choose Close to exit.",
                    "hu": "A program a háttérben fut tovább. A kilépéshez kattints jobb gombbal a tálcaikonra, "
                          "és válaszd a Bezárás lehetőséget."},
    # YouTube live streamers
    "set.youtube_live": {"en": "YouTube live", "hu": "YouTube élő adások"},
    "set.youtube_live_desc": {
        "en": "Monitor YouTube live streamers through the official YouTube Data API v3. Create an API key at "
              "console.cloud.google.com (APIs & Services → enable 'YouTube Data API v3' → Credentials → API key). "
              "The free daily quota covers checking about 10 channels every 2 minutes; live chat polling uses more.",
        "hu": "YouTube-os élő streamerek figyelése a hivatalos YouTube Data API v3-mal. API kulcsot a "
              "console.cloud.google.com oldalon hozhatsz létre (APIs & Services → 'YouTube Data API v3' engedélyezése "
              "→ Credentials → API key). Az ingyenes napi keret kb. 10 csatorna 2 percenkénti ellenőrzésére elég; "
              "az élő chat olvasása ennél többet fogyaszt."},
    "set.yt_api_key": {"en": "YouTube API key", "hu": "YouTube API kulcs"},
    "set.yt_key_set": {"en": "saved (stored encrypted) - type to replace", "hu": "elmentve (titkosítva) - "
                                                                                "írd be az újat a cseréhez"},
    "set.yt_chat": {"en": "Read live chat (aggregate statistics only, no chat logs are stored)",
                    "hu": "Élő chat olvasása (csak összesített statisztika, chatnapló nem készül)"},
    "set.yt_chat_poll": {"en": "Chat check interval", "hu": "Chat ellenőrzési időköz"},
    "str.yt_channel": {"en": "YouTube channel: @handle, channel URL or channel ID",
                       "hu": "YouTube csatorna: @név, csatorna link vagy csatorna ID"},
    "str.yt_hint": {"en": "Example: @mychannel or https://www.youtube.com/@mychannel. Needs the YouTube API key "
                          "(Settings → YouTube live).",
                    "hu": "Például: @csatornam vagy https://www.youtube.com/@csatornam. Kell hozzá a YouTube API "
                          "kulcs (Beállítások → YouTube élő adások)."},
    "str.yt_no_key": {"en": "⚠ YouTube API key missing - YouTube streamers cannot be checked (Settings → YouTube live).",
                      "hu": "⚠ Hiányzik a YouTube API kulcs - a YouTube-os streamerek nem ellenőrizhetők "
                            "(Beállítások → YouTube élő adások)."},
    "str.yt_resolving": {"en": "Looking up the YouTube channel…", "hu": "YouTube csatorna keresése…"},
    # publish accounts
    "acc.title": {"en": "Upload accounts", "hu": "Feltöltési fiókok"},
    "acc.desc": {"en": "Connect as many YouTube channels and TikTok accounts as you like. Each account has its own "
                       "privacy and can receive new clips automatically.",
                 "hu": "Bármennyi YouTube csatornát és TikTok fiókot összekapcsolhatsz. Minden fióknak saját "
                       "láthatósága van, és automatikusan is kaphatja az új klipeket."},
    "acc.add_yt": {"en": "Add YouTube channel", "hu": "YouTube csatorna hozzáadása"},
    "acc.add_tt": {"en": "Add TikTok account", "hu": "TikTok fiók hozzáadása"},
    "acc.none": {"en": "No accounts connected yet.", "hu": "Még nincs összekapcsolt fiók."},
    "acc.auto": {"en": "Auto-upload", "hu": "Automatikus feltöltés"},
    "acc.auto_global": {"en": "Automatically upload new clips to the accounts marked 'Auto-upload'",
                        "hu": "Új klipek automatikus feltöltése az 'Automatikus feltöltés' jelölésű fiókokra"},
    "acc.auto_note": {"en": "Only clips that passed the quality check are uploaded, never duplicates or simulation "
                            "clips, and each clip only once per account.",
                      "hu": "Csak a minőségellenőrzésen átment klipek kerülnek fel, duplikátum és szimuláció soha, "
                            "és minden klip fiókonként csak egyszer."},
    "acc.confirm": {"en": "New clips will be published without asking each time. Do you confirm that you have the "
                          "rights and the streamers' permission to publish their content on these accounts?",
                    "hu": "Az új klipek egyenként rákérdezés nélkül kerülnek fel. Megerősíted, hogy jogod van "
                          "hozzá, és a streamerek engedélyezték a tartalmuk közzétételét ezeken a fiókokon?"},
    "acc.all_streamers": {"en": "All streamers", "hu": "Minden streamer"},
    "acc.streamers": {"en": "Streamers for this account", "hu": "Streamerek ehhez a fiókhoz"},
    "acc.streamers_hint": {"en": "Only clips of the checked streamers go to this account. Nothing checked = all.",
                           "hu": "Csak a bejelölt streamerek klipjei kerülnek erre a fiókra. Ha semmi sincs "
                                 "bejelölve, mindegyiké."},
    "acc.remove_q": {"en": "Disconnect {name}? Videos already uploaded stay online.",
                     "hu": "Leválasztod: {name}? A már feltöltött videók fent maradnak."},
    "acc.need_yt_creds": {"en": "Enter the YouTube client ID (and secret) above first.",
                          "hu": "Előbb add meg fent a YouTube client ID-t (és a secretet)."},
    "acc.need_tt_creds": {"en": "Enter the TikTok client key and secret above first.",
                          "hu": "Előbb add meg fent a TikTok client key-t és secretet."},
    "acc.signing_in": {"en": "Sign in in your browser…", "hu": "Jelentkezz be a böngészőben…"},
    "acc.added": {"en": "Account added: {name}", "hu": "Fiók hozzáadva: {name}"},
    "acc.tt_direct_q": {"en": "Request direct posting (only for TikTok apps approved for it)? "
                              "Choose No to send videos to the TikTok inbox.",
                        "hu": "Kérsz közvetlen posztolást (csak erre jóváhagyott TikTok appokkal megy)? "
                              "Nem = a videók a TikTok postafiókba mennek."},
    # streamers
    "str.add": {"en": "Add streamer", "hu": "Streamer hozzáadása"},
    "str.username": {"en": "Twitch username", "hu": "Twitch felhasználónév"},
    "str.authorized": {"en": "I am authorized to record and redistribute this channel's content",
                       "hu": "Jogosult vagyok a csatorna tartalmának rögzítésére és megosztására"},
    "str.start": {"en": "Start monitoring", "hu": "Figyelés indítása"},
    "str.start_all": {"en": "Start all", "hu": "Összes indítása"},
    "str.stop_all": {"en": "Stop all", "hu": "Összes leállítása"},
    "str.all_tip": {"en": "Start or stop monitoring every streamer at once",
                    "hu": "Az összes streamer figyelésének indítása vagy leállítása egyszerre"},
    "str.stop_all_q": {"en": "Live recordings are running. Stopping saves pending clips safely. Stop all?",
                       "hu": "Élő felvétel fut. A leállítás biztonságosan elmenti a függő klipeket. Mindet leállítsam?"},
    "str.stop": {"en": "Stop monitoring", "hu": "Figyelés leállítása"},
    "str.remove": {"en": "Remove", "hu": "Eltávolítás"},
    "str.live": {"en": "LIVE", "hu": "ÉLŐ"},
    "str.offline": {"en": "OFFLINE", "hu": "OFFLINE"},
    "str.recording": {"en": "Recording", "hu": "Felvétel"},
    "str.highlights": {"en": "highlights", "hu": "kiemelés"},
    "str.empty": {"en": "No streamers yet. Add an authorized Twitch channel to begin.",
                  "hu": "Még nincs streamer. Adj hozzá egy engedélyezett Twitch csatornát."},
    # live
    "live.chat_rate": {"en": "Chat messages / s", "hu": "Chat üzenet / mp"},
    "live.audio": {"en": "Audio activity", "hu": "Hangaktivitás"},
    "live.score": {"en": "Highlight score", "hu": "Kiemelési pontszám"},
    "live.buffer": {"en": "Recording buffer", "hu": "Felvételi puffer"},
    "live.events": {"en": "Latest detections", "hu": "Legutóbbi észlelések"},
    "live.select": {"en": "Select a streamer", "hu": "Válassz streamert"},
    "live.timeline": {"en": "Highlight timeline", "hu": "Kiemelési idővonal"},
    # library
    "lib.search": {"en": "Search clips…", "hu": "Klipek keresése…"},
    "lib.min_score": {"en": "Min score", "hu": "Min. pont"},
    "lib.sort_new": {"en": "Newest", "hu": "Legújabb"},
    "lib.sort_score": {"en": "Highest score", "hu": "Legmagasabb pont"},
    "lib.open_editor": {"en": "Edit", "hu": "Szerkesztés"},
    "lib.delete": {"en": "Delete", "hu": "Törlés"},
    "lib.play": {"en": "Play", "hu": "Lejátszás"},
    "lib.folder": {"en": "Show file", "hu": "Fájl megnyitása"},
    "lib.empty": {"en": "No clips yet. They will appear here automatically.",
                  "hu": "Még nincsenek klipek. Automatikusan meg fognak jelenni."},
    "lib.confirm_delete": {"en": "Delete this clip and its files? Exports are kept unless you tick the box.",
                           "hu": "Törlöd a klipet és a fájljait? Az exportok megmaradnak, hacsak be nem jelölöd."},
    # editor
    "ed.trim": {"en": "Trim", "hu": "Vágás"},
    "ed.start": {"en": "Start", "hu": "Kezdet"},
    "ed.end": {"en": "End", "hu": "Vége"},
    "ed.layout": {"en": "Layout", "hu": "Elrendezés"},
    "ed.profile": {"en": "Editing profile", "hu": "Szerkesztési profil"},
    "ed.subtitles": {"en": "Subtitles", "hu": "Feliratok"},
    "ed.sub_style": {"en": "Subtitle style", "hu": "Felirat stílus"},
    "ed.transcribe": {"en": "Transcribe", "hu": "Átírás"},
    "ed.render": {"en": "Render video", "hu": "Videó renderelése"},
    "ed.preview_frame": {"en": "Preview frame", "hu": "Képkocka előnézet"},
    "ed.before": {"en": "Original", "hu": "Eredeti"},
    "ed.after": {"en": "Edited", "hu": "Szerkesztett"},
    "ed.facecam": {"en": "Facecam region", "hu": "Facecam terület"},
    "ed.crop_x": {"en": "Crop center X", "hu": "Vágás középpont X"},
    "ed.volume": {"en": "Volume", "hu": "Hangerő"},
    "ed.normalize": {"en": "Normalize audio", "hu": "Hang normalizálása"},
    "ed.no_clip": {"en": "Open a clip from the library to edit it.",
                   "hu": "Nyiss meg egy klipet a könyvtárból a szerkesztéshez."},
    "ed.metadata": {"en": "Title & description", "hu": "Cím és leírás"},
    "ed.publish": {"en": "Publish…", "hu": "Közzététel…"},
    "ed.export_folder": {"en": "Open exports", "hu": "Exportok megnyitása"},
    # queue
    "q.pause": {"en": "Pause", "hu": "Szünet"},
    "q.resume": {"en": "Resume", "hu": "Folytatás"},
    "q.cancel": {"en": "Cancel", "hu": "Mégse"},
    "q.retry": {"en": "Retry", "hu": "Újra"},
    "q.empty": {"en": "Queue is empty.", "hu": "A sor üres."},
    # settings
    "set.save": {"en": "Save settings", "hu": "Beállítások mentése"},
    "set.saved": {"en": "Settings saved.", "hu": "Beállítások elmentve."},
    "rc.title": {"en": "Remote control (iPhone)", "hu": "Távirányító (iPhone)"},
    "rc.desc": {"en": "Control DOGSAPKA from the iPhone app over your home network: live status, start/stop "
                      "monitoring, watch and upload clips, processing queue. Only paired phones get access.",
                "hu": "Vezéreld a DOGSAPKA-t az iPhone appból az otthoni hálózaton: élő állapot, figyelés "
                      "indítása/leállítása, klipek megnézése és feltöltése, feldolgozási sor. Csak a párosított "
                      "telefonok férnek hozzá."},
    "rc.enable": {"en": "Enable remote control", "hu": "Távirányító bekapcsolása"},
    "rc.port": {"en": "Port", "hu": "Port"},
    "rc.pair": {"en": "Pair an iPhone", "hu": "iPhone párosítása"},
    "rc.pair_title": {"en": "Pair your iPhone", "hu": "iPhone párosítása"},
    "rc.pair_steps": {"en": "Open the DOGSAPKA app on your iPhone (same Wi-Fi as this computer), tap Pair and scan "
                            "this QR code - or type the address and code below.",
                      "hu": "Nyisd meg a DOGSAPKA appot az iPhone-on (ugyanazon a Wi-Fi-n, mint ez a gép), koppints a "
                            "Párosítás gombra, és olvasd be ezt a QR-kódot - vagy írd be lent a címet és a kódot."},
    "rc.manual": {"en": "Address: {addr}", "hu": "Cím: {addr}"},
    "rc.valid": {"en": "Code valid for {m}:{s:02d}", "hu": "A kód még {m}:{s:02d}-ig érvényes"},
    "rc.expired": {"en": "The code expired - close and open pairing again.",
                   "hu": "A kód lejárt - zárd be, és indítsd újra a párosítást."},
    "rc.on": {"en": "✔ Running - reachable at {addr}", "hu": "✔ Fut - elérhető itt: {addr}"},
    "rc.off": {"en": "Off. Tick 'Enable remote control' and save to start it.",
               "hu": "Kikapcsolva. Jelöld be a 'Távirányító bekapcsolása' négyzetet, és mentsd a beállításokat."},
    "rc.devices": {"en": "Paired devices", "hu": "Párosított eszközök"},
    "rc.none": {"en": "No paired device yet.", "hu": "Még nincs párosított eszköz."},
    "rc.last_seen": {"en": "last seen {t}", "hu": "utoljára: {t}"},
    "rc.revoke": {"en": "Revoke", "hu": "Letiltás"},
    "rc.revoke_q": {"en": "Revoke access for {name}? The phone has to be paired again.",
                    "hu": "Letiltod ezt az eszközt: {name}? A telefont újra párosítani kell."},
    "rc.note": {"en": "Works on your local network. Windows asks once whether to allow DOGSAPKA through the "
                      "firewall - allow it for private networks. For access from outside home, use a VPN such as "
                      "Tailscale; never forward the port to the internet.",
                "hu": "A helyi hálózaton működik. A Windows egyszer megkérdezi, átengedje-e a tűzfal a DOGSAPKA-t - "
                      "engedélyezd a magánhálózatokon. Otthonon kívülről VPN-nel (pl. Tailscale) érd el; a portot "
                      "soha ne nyisd ki az internet felé."},
    "live.view_all": {"en": "All live", "hu": "Összes élő"},
    "live.view_one": {"en": "One streamer", "hu": "Egy streamer"},
    "live.none": {"en": "No streamer is live right now. Monitored streamers appear here automatically when they "
                        "go live - all of them side by side.",
                  "hu": "Most egy streamer sincs élőben. A figyelt streamerek automatikusan megjelennek itt, amikor "
                        "élőben vannak - mind egymás mellett."},
    "live.tile_tip": {"en": "Click for the detailed view", "hu": "Kattints a részletes nézethez"},
    "live.waiting": {"en": "Waiting for the first recorded seconds…", "hu": "Várakozás az első felvett másodpercekre…"},
    "live.audio_short": {"en": "Audio", "hu": "Hang"},
    "q.open_tip": {"en": "Click to open the finished video", "hu": "Kattints a kész videó megnyitásához"},
    "q.open_video": {"en": "Play video", "hu": "Videó lejátszása"},
    "q.open_folder": {"en": "Show in folder", "hu": "Megnyitás mappában"},
    "set.reset": {"en": "Reset to defaults", "hu": "Alaphelyzet"},
    "set.reset_tip": {"en": "Restore the default settings", "hu": "Gyári beállítások visszaállítása"},
    "set.reset_q": {"en": "Reset all settings to their defaults?\n\nKept: Twitch / YouTube / TikTok client IDs, API "
                          "keys and connected accounts, language, data folder, your streamers and clips.",
                    "hu": "Visszaállítod az összes beállítást az alapértékre?\n\nMegmarad: Twitch / YouTube / TikTok "
                          "client ID-k, API kulcsok és összekötött fiókok, nyelv, adatmappa, a streamereid és "
                          "klipjeid."},
    "set.reset_done": {"en": "Default settings restored.", "hu": "Alapbeállítások visszaállítva."},
    "upd.title": {"en": "New version available", "hu": "Új verzió érhető el"},
    "upd.title_none": {"en": "Updates", "hu": "Frissítések"},
    "upd.versions": {"en": "Version {new} is available - you have {cur}.",
                     "hu": "Elérhető a(z) {new} verzió - neked most a(z) {cur} van."},
    "upd.no_notes": {"en": "No release notes.", "hu": "Nincs leírás a változásokról."},
    "upd.install": {"en": "Download and install", "hu": "Letöltés és telepítés"},
    "upd.open_page": {"en": "Open download page", "hu": "Letöltési oldal megnyitása"},
    "upd.later": {"en": "Later", "hu": "Később"},
    "upd.skip": {"en": "Skip this version", "hu": "Ezt a verziót kihagyom"},
    "upd.downloading": {"en": "Downloading…", "hu": "Letöltés…"},
    "upd.installing": {"en": "Installing - the app restarts by itself in a moment.",
                       "hu": "Telepítés - a program magától újraindul pár másodperc múlva."},
    "upd.live_running": {"en": "Live recordings are running. Updating stops them safely (pending clips are saved). "
                               "Update now?",
                         "hu": "Élő felvétel fut. A frissítés biztonságosan leállítja (a függő klipek mentődnek). "
                               "Frissítsek most?"},
    "upd.manual_mac": {"en": "The disk image is open. Drag DOGSAPKA AI CLIPPER into Applications and replace the "
                             "old version.",
                       "hu": "Megnyílt a lemezkép. Húzd a DOGSAPKA AI CLIPPER-t az Alkalmazások (Applications) mappába, "
                             "és cseréld le a régit."},
    "upd.up_to_date": {"en": "You have the latest version ({cur}).", "hu": "A legfrissebb verzió van fent ({cur})."},
    "upd.auto": {"en": "Check for updates at startup", "hu": "Frissítések keresése induláskor"},
    "upd.check_now": {"en": "Check now", "hu": "Keresés most"},
    "set.censor": {"en": "Mask swear words in subtitles (F**K, k***a)",
                   "hu": "Csúnya szavak kicsillagozása a feliratban (F**K, k***a)"},
    "lnk.twitch_create": {"en": "Register a Twitch app", "hu": "Twitch app regisztrálása"},
    "lnk.twitch_apps": {"en": "My Twitch apps (Client ID)", "hu": "Twitch appjaim (Client ID)"},
    "lnk.yt_enable": {"en": "Enable YouTube Data API v3", "hu": "YouTube Data API v3 bekapcsolása"},
    "lnk.yt_key": {"en": "Create API key (Credentials)", "hu": "API kulcs létrehozása (Credentials)"},
    "lnk.yt_quota": {"en": "Quota usage", "hu": "Keret (kvóta) használat"},
    "lnk.g_clients": {"en": "OAuth clients (Desktop app: ID + secret)", "hu": "OAuth kliensek (Desktop app: ID + secret)"},
    "lnk.g_testers": {"en": "Test users / Publish app", "hu": "Tesztelők / Publish app"},
    "lnk.tt_apps": {"en": "TikTok developer apps (client key + secret)",
                    "hu": "TikTok fejlesztői appok (client key + secret)"},
    "lnk.tt_docs": {"en": "Content Posting API guide", "hu": "Content Posting API leírás"},
    "set.data_folder": {"en": "Data folder", "hu": "Adatmappa"},
    "set.data_change": {"en": "Change…", "hu": "Módosítás…"},
    "set.data_pick": {"en": "Choose a drive or folder for clips, recordings and exports",
                      "hu": "Válassz meghajtót vagy mappát a klipeknek, felvételeknek és exportoknak"},
    "set.data_nested": {"en": "The new folder cannot be inside the current data folder (or the other way round).",
                        "hu": "Az új mappa nem lehet a mostani adatmappán belül (és fordítva sem)."},
    "set.data_move_q": {"en": "New data folder:\n{new}\n\nMove the existing clips, recordings, exports and settings "
                              "there ({size:.1f} GB)?\n\nYes = move everything\nNo = start empty in the new folder "
                              "(the old data stays where it is)",
                        "hu": "Új adatmappa:\n{new}\n\nÁtköltöztessem oda a meglévő klipeket, felvételeket, "
                              "exportokat és beállításokat ({size:.1f} GB)?\n\nIgen = mindent átviszek\nNem = az új "
                              "mappa üresen indul (a régi adatok a helyükön maradnak)"},
    "set.data_nospace": {"en": "Not enough free space there: {free:.1f} GB free, {size:.1f} GB needed.",
                         "hu": "Nincs ott elég szabad hely: {free:.1f} GB szabad, {size:.1f} GB kellene."},
    "set.data_after_restart": {"en": "after restart", "hu": "újraindítás után"},
    "set.data_restart_q": {"en": "The new folder is used after a restart (data is moved during startup). Restart now?",
                           "hu": "Az új mappát újraindítás után használja a program (az áthelyezés indításkor "
                                 "történik). Újraindítsam most?"},
    "set.twitch": {"en": "Twitch", "hu": "Twitch"},
    "set.recording": {"en": "Recording", "hu": "Felvétel"},
    "set.detection": {"en": "Highlight detection", "hu": "Kiemelés észlelés"},
    "set.output": {"en": "Video output", "hu": "Videó kimenet"},
    "set.ai": {"en": "AI & subtitles", "hu": "AI és feliratok"},
    "set.interface": {"en": "Interface", "hu": "Felület"},
    "set.publishing": {"en": "Publishing", "hu": "Közzététel"},
    "set.connect": {"en": "Connect Twitch account", "hu": "Twitch fiók csatlakoztatása"},
    "set.restart": {"en": "Language changes apply after restart.", "hu": "A nyelv újraindítás után változik."},
    # wizard
    "wiz.title": {"en": "Welcome to DOGSAPKA AI CLIPPER", "hu": "Üdv a DOGSAPKA AI CLIPPER-ben"},
    "wiz.check": {"en": "System check", "hu": "Rendszer ellenőrzés"},
    "wiz.recheck": {"en": "Check again", "hu": "Újraellenőrzés"},
    "wiz.finish": {"en": "Get started", "hu": "Kezdés"},
    # generic
    "ok": {"en": "OK", "hu": "OK"},
    "cancel": {"en": "Cancel", "hu": "Mégse"},
    "error": {"en": "Error", "hu": "Hiba"},
}


def set_language(lang: str) -> None:
    global _LANG
    _LANG = lang if lang in ("en", "hu") else "en"


def language() -> str:
    return _LANG


def tr(key: str, **fmt) -> str:
    entry = STRINGS.get(key)
    text = (entry.get(_LANG) or entry.get("en")) if entry else key
    return text.format(**fmt) if fmt else text
