# Sprint 1: Stream Latency & Sync — Samenvatting

Gebruikersvriendelijke toelichting op het technische plan (`PLAN_sprint1_stream_latency.md`).

## Wat is het probleem?

De NerdCam web viewer heeft twee fundamentele problemen:

1. **Audio en video lopen niet synchroon** — Video komt via een MJPEG `<img>` element (~1 seconde vertraging). Audio komt via een apart MP3 `<Audio>` element (~5 seconden vertraging). Dat is een verschil van ~4 seconden. Dit kan niet opgelost worden zonder de architectuur te veranderen, omdat het twee compleet losse streams zijn.

2. **Elke ~4 minuten 35 seconden bevriest het beeld** — De Foscam R2 camera verbreekt automatisch de RTSP-verbinding na ~275 seconden. NerdCam detecteert dit (na 5s wachttijd) en start opnieuw (duurt ~2s). Totale zichtbare freeze: ~7 seconden. Dit is camera-firmware gedrag en treft zowel de web viewer als NerdPudding.

Beide problemen zijn architecturaal — ze vereisen codewijzigingen, geen instellingen.

**Belangrijk:** `/api/mjpeg` wordt NOOIT aangepast. NerdPudding hangt daar vanaf met een custom boundary parser.

---

## Wat gaan we doen?

### Punt 1: TCP als standaard transport

**Probleem:** De standaard staat op UDP. Na elke 275s camera-timeout mislukt de UDP-herstart soms 1-4 keer — ffmpeg vindt dan alleen audio, geen video. Dat geeft tot 20 seconden blackout in plaats van ~7s. Dit bleek uit analyse van de logs op 2026-02-19: TCP sessies hadden nul mislukte restarts, UDP sessies hadden er meerdere.

**Oplossing:** Default waarde van het RTSP transport wijzigen van `"udp"` naar `"tcp"`. Bestaande configs behouden hun huidige instelling (wordt uit de encrypted config geladen).

**Waarom werkt het:** TCP heeft een grotere probesize (500KB vs 32KB) en de RTSP data komt interleaved — ffmpeg vindt altijd beide streams (video + audio). Op een lokaal netwerk is er geen merkbaar verschil in vloeiendheid of latency tussen TCP en UDP.

**Nadelen:** ~0.5s langere opstarttijd door grotere probesize. Theoretisch kan TCP stallen bij netwerkverlies, maar op een LAN is dat irrelevant.

**Voordelen:** Betrouwbaardere recovery na de 275s timeout. Minder blackout voor zowel de web viewer als NerdPudding.

**Werk:** Eén regel code.

---

### Punt 2: Stale detection threshold verlagen (5s naar 2s)

**Probleem:** Als de camera de RTSP-verbinding verbreekt (elke ~275s), duurt het 5 seconden voordat NerdCam het detecteert. Plus ~2s herstart = ~7s totale freeze. In de browser voelt dat lang.

**Oplossing:** De stale detection drempel verlagen van 5 naar 2 seconden. Zowel server-side (de constante) als client-side (de frame-teller die bij 250 staat, naar 100 — dat is 100 x 20ms = 2s).

**Waarom werkt het:** Bij 25fps komen er elke 40ms frames binnen. Als er 2 seconden geen enkel frame komt, is de stream definitief dood. De camera stopt abrupt (hard cut, geen graduale degradatie), dus 2s is ruim genoeg om vals-positieven te vermijden.

**Nadelen:** Als PTZ-beweging een korte pauze veroorzaakt (>2s), kan dat een onnodige restart triggeren. Met TCP als default is dit niet waargenomen. Mocht het optreden, is 3s een veilige fallback.

**Voordelen:** Freeze gaat van ~7s naar ~4s. Gecombineerd met TCP (betrouwbare eerste restart) is de totale downtime minimaal.

**Werk:** Twee regels code.

---

### Punt 3: PTZ preset parsing fix

**Probleem:** De functie die PTZ-presets ophaalt (`ptz_list_presets`) leest alleen de eerste preset (`point0`). Als je 3-4 presets hebt opgeslagen op de camera, zie je er maar één in de CLI. De camera stuurt `point0`, `point1`, `point2`, etc. terug, maar de code kijkt alleen naar `point0`.

**Oplossing:** Itereer over alle keys die beginnen met `"point"` gevolgd door een cijfer. Sorteer ze zodat ze in de juiste volgorde staan. URL-decode de namen (camera stuurt gecodeerde tekens).

**Waarom werkt het:** De camera CGI retourneert een platte lijst met `point0` t/m `pointN`. Door te itereren in plaats van alleen `point0` te lezen, pakken we ze allemaal.

**Nadelen:** Geen. Het is een bugfix — de functie deed het gewoon niet goed.

**Voordelen:** CLI toont alle presets correct. Belangrijk voor patrol-configuratie (je moet weten welke presets er zijn).

**Werk:** ~5 regels code vervangen.

---

### Punt 4: RTSP keepalive onderzoek

**Probleem:** De Foscam R2 verbreekt elke ~275s de RTSP-verbinding. Dit is firmware-gedrag. Elke recovery (zelfs met TCP + threshold 2s) betekent ~4s freeze en een nieuwe ffmpeg opstart. Dat is elke 4,5 minuut.

**Oplossing:** Onderzoek in volgorde, stop zodra iets werkt:
- **4a: Camera-instelling** — Via CGI-commando's uitzoeken of de camera een instelbare RTSP timeout heeft. Als dat zo is, zetten we die hoog. Nul code nodig.
- **4b: ffmpeg parameter** — Een socket timeout parameter (`-stimeout`) toevoegen die ffmpeg dwingt de verbinding open te houden.
- **4c: Keepalive thread** — Een Python achtergrondthread die elke 60 seconden een RTSP keepalive-bericht stuurt. Nadeel: werkt buiten ffmpeg's sessie, dus waarschijnlijk niet effectief.
- **4d: Accepteren** — Als niets werkt, is ~4s freeze elke 275s met automatische recovery draaglijk.

**Waarom zou het werken:** De camera heeft waarschijnlijk een idle-timeout. Als we de sessie actief houden of de timeout hoger zetten, stopt het probleem. Maar het is onzeker — vandaar het onderzoekskarakter.

**Nadelen:** Kan niks opleveren (optie 4d). Dan hebben we in elk geval de quick wins (punt 1-3) als vangnet.

**Voordelen:** Als het lukt: nul freezes, stabiele stream. Grootste potentiële winst van alles.

**Werk:** Onderzoek. 4a is een paar CGI queries. 4b is een parameter toevoegen. 4c is ~40 regels code. Kan ook uitkomen op "niets werkt, we accepteren het."

---

### Punt 5: Nieuw `/api/fmp4` endpoint (server-side)

**Probleem:** Audio en video zijn nu twee losse streams. De MJPEG `<img>` heeft ~1s vertraging, het MP3 `<Audio>` element ~5s. Ze kunnen niet gesynchroniseerd worden omdat het aparte ffmpeg processen zijn met elk een eigen RTSP-verbinding naar de camera.

**Oplossing:** Een nieuw endpoint `/api/fmp4` dat één ffmpeg proces start. Dit pakt de RTSP stream, kopieert de H.264 video (geen re-encoding) en zet de audio om naar AAC. Het geheel wordt als "fragmented MP4" naar de browser gestuurd — een speciaal formaat dat streambaar is (de browser hoeft niet te wachten op het einde van het bestand).

**Waarom werkt het:** Eén ffmpeg proces = één RTSP-sessie = video en audio komen uit dezelfde bron en zijn inherent synchroon. Fragmented MP4 is het formaat dat browsers via MediaSource Extensions (punt 6) kunnen afspelen.

**Nadelen:** Eén extra RTSP-sessie per browser client. De fMP4 stream is niet deelbaar zoals MJPEG (elke client krijgt een eigen ffmpeg proces). Geen re-encoding betekent minimale CPU-belasting.

**Voordelen:** Gesynchroniseerde A/V naar de browser. Verwachte latency <2 seconden.

**Werk:** ~40 regels Python, vrijwel identiek aan de bestaande `/api/stream` handler maar met andere ffmpeg flags.

---

### Punt 6: MSE in de browser (JavaScript)

**Probleem:** De browser kan het fMP4 endpoint niet zomaar afspelen. Een gewone `<video src="/api/fmp4">` werkt niet omdat de browser het hele bestand wil downloaden voordat het afspeelt.

**Oplossing:** JavaScript code met MediaSource Extensions (MSE). Dit is een browser-API die ontworpen is voor live streaming — dezelfde technologie die YouTube en Netflix gebruiken. De code fetcht het fMP4 als een stream, voert stukjes toe aan een videobuffer, en speelt het af in een `<video>` element. Inclusief:
- Automatische codec-detectie (probeert meerdere H.264 profielen)
- Buffer management (houdt laatste 10 seconden, ruimt oud materiaal op)
- Auto-reconnect na stream-einde of fouten (3 seconden vertraging)
- Fallback naar de oude MJPEG `<img>` methode als MSE niet ondersteund wordt
- Audio toggle werkt dan via `video.muted` in plaats van een apart element

**Waarom werkt het:** MSE is exact hiervoor ontworpen. Chrome, Firefox en Edge ondersteunen het allemaal.

**Nadelen:** ~200 regels JavaScript, complexer dan de huidige simpele `<img>` aanpak. MSE werkt niet op alle mobiele browsers (iOS Safari heeft beperkingen). De MJPEG fallback vangt dat op.

**Voordelen:** Gesynchroniseerde audio en video in één element. Latency <2s voor beide. Geen apart audio-element meer nodig.

**Werk:** ~200 regels JavaScript + HTML wijzigingen.

---

### Punt 7: Integratietest en NerdPudding verificatie

**Probleem:** Na al deze wijzigingen moeten we verifiëren dat niets kapot is — vooral `/api/mjpeg` voor NerdPudding.

**Oplossing:** Handmatige verificatie:
- Bevestig via `git diff` dat de MJPEG handler en bron onaangeraakt zijn
- Test dat `/api/mjpeg` valide output geeft met de juiste boundaries
- Web viewer werkt in Chrome (MSE) en Firefox (MSE of fallback)
- A/V sync: minder dan 2 seconden latency, video en audio lopen gelijk
- Stream herstelt automatisch na de 275s camera timeout
- Recording werkt naast MSE + MJPEG (3 gelijktijdige RTSP sessies)
- MJPEG fallback werkt als MSE niet beschikbaar is

**Nadelen:** Geen — het is verificatie, geen code.

**Voordelen:** Zekerheid dat NerdPudding blijft werken en niets regresseert.

**Werk:** Handmatig testen.

---

## Volgorde en logica

| Fase | Punten | Karakter |
|------|--------|----------|
| Quick wins | 1, 2, 3 | Kleine wijzigingen met direct resultaat |
| Onderzoek | 4 | Kan de 275s timeout elimineren, of we accepteren het |
| Architectuur | 5, 6 | De grote verandering: gesynchroniseerde A/V |
| Verificatie | 7 | Alles testen, NerdPudding contract checken |

De quick wins worden eerst gedaan omdat ze weinig risico hebben en direct verbetering geven. Het onderzoek (punt 4) bepaalt of de freezes helemaal weg kunnen. De architectuurwijziging (punt 5+6) lost het sync-probleem op. De integratietest sluit af.

## Verschil met het technische plan

Deze samenvatting bevat één aanvulling ten opzichte van `PLAN_sprint1_stream_latency.md`:

- **Punt 1 (TCP als default)** is nieuw. Dit komt uit de stream-debugger analyse van 2026-02-19, die aantoonde dat UDP-restarts na de 275s timeout onbetrouwbaar zijn (1-4 mislukte pogingen). Het technische plan is hierop bijgewerkt.

## Architectuur na implementatie

```
Browser (MSE):        Camera --> RTSP --> ffmpeg (H.264 copy + AAC) --> /api/fmp4 --> <video> (gesyncte A/V)
Browser (fallback):   Camera --> RTSP --> ffmpeg (H.264-->MJPEG) --> /api/mjpeg --> <img> (alleen video)
                      Camera --> RTSP --> ffmpeg (audio-->MP3) --> /api/audio --> <Audio> (niet gesynct)
NerdPudding:          Camera --> RTSP --> [gedeeld ffmpeg] --> /api/mjpeg --> NerdPudding (ongewijzigd)
```
