# Temperature BLE/DHT22 to MQTT

Ce conteneur écoute les annonces BLE des quatre capteurs configurés dans
`app.py`, lit un DHT22 sur D4 et publie leurs mesures dans MQTT toutes les cinq
minutes.
Il n'ouvre pas de connexion GATT permanente : Ruuvi, SensorBlue/ThermoBeacon et
Inkbird diffusent leurs mesures directement, ce qui limite la consommation des
piles.

## Capteurs

| Nom | Adresse | Protocole |
| --- | --- | --- |
| Ça pique | `E3:EE:E4:14:FA:B0` | Ruuvi |
| Avalanche Toit | `9D:88:00:00:02:2C` | SensorBlue/ThermoBeacon |
| Fruit Storage | `49:22:11:08:18:64` | Inkbird |
| Tête used | `49:22:09:05:14:A1` | Inkbird |
| DHT22 | `GPIO D4` | DHT22 filaire |

## Construction et lancement

Depuis la racine du dépôt :

```sh
docker build -t ch.bus.temperature-mqtt/temperature:latest ./temperature

docker run -d \
  --restart=always \
  --name temperature-mqtt \
  --net=host \
  --privileged \
  -v /var/run/dbus:/var/run/dbus \
  -e MQTT_HOST="127.0.0.1" \
  -e MQTT_PORT="1883" \
  -e MQTT_USERNAME="victron" \
  -e MQTT_PASSWORD="CHANGE_ME" \
  -e MQTT_BASE_TOPIC="van/temperature" \
  -e READ_INTERVAL_SECONDS="300" \
  -e SCAN_TIMEOUT_SECONDS="45" \
  -e MISSED_CYCLES_BEFORE_OFFLINE="3" \
  -e DHT22_TEMPERATURE_OFFSET="-4" \
  ch.bus.temperature-mqtt/temperature:latest
```

`--net=host`, `--privileged` et le montage DBus donnent au conteneur accès à
l'adaptateur Bluetooth du Raspberry Pi.

Le scanner BLE reste actif en continu. `SCAN_TIMEOUT_SECONDS` limite uniquement
l'attente du premier relevé au démarrage ; les publications suivantes utilisent
les annonces reçues pendant tout le cycle `READ_INTERVAL_SECONDS`. Un capteur ne
passe hors ligne qu'après `MISSED_CYCLES_BEFORE_OFFLINE` cycles consécutifs sans
annonce valide.

## Topics MQTT

Le JSON complet de chaque capteur est publié avec QoS 1 et `retain` :

```text
van/temperature/ca_pique
van/temperature/avalanche_toit
van/temperature/fruit_storage
van/temperature/tete_used
van/temperature/dht22
```

Chaque valeur scalaire dispose aussi de son propre topic, par exemple :

```text
van/temperature/fruit_storage/temperature
van/temperature/fruit_storage/humidity
van/temperature/fruit_storage/battery
van/temperature/fruit_storage/rssi
```

Topics de fonctionnement :

```text
van/temperature/status
van/temperature/scan
van/temperature/<capteur>/availability
```

`scan` contient le nombre de capteurs trouvés et la liste des absents lors du
dernier cycle. Les valeurs `availability` sont `online` ou `offline`.

Pour observer toutes les publications :

```sh
mosquitto_sub -h 127.0.0.1 -u victron -P 'CHANGE_ME' -t 'van/temperature/#' -v
```
