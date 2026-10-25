# OpenLisaPro

[English](README.md) | Deutsch

OpenLisaPro ist ein selbst gehostetes Flask-Dashboard und ein Python-Client für Lisa-Pro- und LuminaGrowX-Growboxen. Es zeigt Klima- und Gerätestatus in Echtzeit an, verwaltet lokale Steuerungseinstellungen und kann einen VPD-basierten Lüfterregler ausführen.

Der Browser kommuniziert mit der lokalen Flask-Anwendung, die Anfragen an die Growbox weiterleitet. Aktuelle lokale Steuereinstellungen bleiben in `control.json`. Sensormessungen, Reglerstatus, Einstellungssnapshots und Reglerereignisse werden dauerhaft in `openlisapro.sqlite3` gespeichert. Vorhandene Daten aus `controller-history.jsonl` und `flow-events.json` werden beim Start einmalig importiert; die alten Dateien bleiben erhalten. Das Diagramm zeigt weiterhin die letzten 48 Stunden. Den vollständigen Datensatz kannst du über `/api/data/export?format=jsonl` oder `/api/data/export?format=csv` herunterladen.

## Funktionen

- Temperatur, Luftfeuchtigkeit, Taupunkt, VPD, Wachstumsphase und Gerätestatus innen und außen überwachen.
- Lüftergrenzen, VPD-Ziele, Temperaturgrenzen, LED-Stufen und Lichtpläne je Wachstumsphase konfigurieren.
- Optional die Abluftgeschwindigkeit mit einem lokalen PID-Regler und Temperaturschutz steuern.
- Im vollständigen manuellen Modus Lüfter- und LED-Prozentwerte direkt vorgeben; PID-Regelung und Temperaturüberschreibungen werden dabei umgangen.
- Sensorwerte für Temperatur und Luftfeuchtigkeit mit einer Nachkommastelle anzeigen, sofern das Gerät entsprechende Werte liefert.
- Die Lüftergeschwindigkeit oberhalb der weichen Temperaturgrenze einer Phase schrittweise erhöhen, bis die Temperatur sinkt; jede Phase hat eine eigene Obergrenze.
- Ein Zwei-Tage-Diagramm für Lüfter und VPD sowie den vollständigen Verlauf der Reglerereignisse anzeigen.
- Sensordaten, Gerätestatus, Reglerzustand und die zu jeder Messung aktiven Einstellungen dauerhaft in SQLite speichern und als JSON Lines oder CSV exportieren.
- Zwischen Deutsch und Englisch wechseln.
- Mit `LisaProClient` über Python auf die Geräte-API zugreifen.

## Schnellstart

Benötigt werden Python 3.9+, [`uv`](https://docs.astral.sh/uv/) und eine erreichbare Growbox für Live-Gerätefunktionen.

```bash
uv sync
LISA_PRO_URL=http://192.168.1.50 uv run lisa-pro-ui
```

## Hochverfügbarkeit

Betreibe einen OpenLisaPro-Primärknoten und eine Replik, die beide mit derselben Growbox verbunden sind. Alle fünf Sekunden synchronisiert die Replik die Konfiguration des Primärknotens, SQLite-Messungen, Reglerereignisse und die zugehörigen Einstellungssnapshots. Solange der Primärknoten erreichbar ist, sind die Steuerelemente der Replik schreibgeschützt. Wird der Primärknoten unerreichbar, übernimmt die Replik die Regelung. Sobald der Primärknoten zurückkehrt, wechselt die Replik wieder in den schreibgeschützten Modus. Beim Primärknoten gilt standardmäßig `LISA_PRO_HA_ROLE=primary`. Beide Instanzen benötigen Netzwerkzugriff auf die Growbox; außerdem muss die Replik den Primärknoten erreichen können. Alle HA-Variablen und weitere Hinweise stehen unter [Installation und Konfiguration](docs/installation.md).

Konfiguriere die Replik mit derselben `LISA_PRO_URL`, einem eigenen Datenverzeichnis, ihrer Replik-Rolle und der URL des Primärknotens:

```bash
LISA_PRO_URL=http://192.168.1.50 \
LISA_PRO_DATA=./replica-data \
LISA_PRO_HA_ROLE=replica \
LISA_PRO_HA_MASTER_URL=http://192.168.1.51:5050 \
uv run lisa-pro-ui
```

## Dokumentation

- [Installation und Konfiguration](docs/installation.md)
- [Lokale Lüftersteuerung](docs/control.md)
- [Spezifikationen](docs/specifications.md)
- [Python-Client](docs/python-client.md)
- [Entwicklung, Tests und Release-Builds](docs/development.md)
- [Änderungsprotokoll](CHANGELOG.md)
- [HTTP-API-Referenz der Growbox](openapi.yaml)

## Screenshots

### Englisch

![OpenLisaPro-Dashboard auf Englisch in der Desktop-Ansicht](docs/screenshots/dashboard-desktop.png)

![OpenLisaPro-Dashboard auf Englisch in der mobilen Ansicht](docs/screenshots/dashboard-mobile.png)

### Deutsch

![OpenLisaPro-Dashboard auf Deutsch in der Desktop-Ansicht](docs/screenshots/dashboard-desktop-de.png)

![OpenLisaPro-Dashboard auf Deutsch in der mobilen Ansicht](docs/screenshots/dashboard-mobile-de.png)
