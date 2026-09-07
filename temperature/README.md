# Collecteur DHT22 vers MQTT

Ce conteneur ne lit que le DHT22 câblé sur D4 (GPIO 4). Toutes les sondes BLE
Ruuvi, SensorBlue et Inkbird sont collectées par `ch.bus.bluetooth-mqtt`.
Le chemin et le nom d'image `temperature/` sont conservés pour le déploiement.

```sh
docker build -t ch.bus.temperature-mqtt/temperature:latest ./temperature
docker run -d --restart=always --name temperature-mqtt \
  --net=host --device /dev/gpiomem:/dev/gpiomem \
  --env-file mqtt.env --stop-timeout 15 \
  ch.bus.temperature-mqtt/temperature:latest
```

`mqtt.env` contient uniquement `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME` et
`MQTT_PASSWORD`. Aucun accès DBus, aucune dépendance BLE et aucun privilège
Bluetooth ne sont nécessaires. Le périphérique GPIO précis dépend du Raspberry
Pi et du backend GPIO ; cette commande conserve le backend RPi.GPIO existant,
à vérifier sur le matériel cible.

| Variable | Défaut |
| --- | --- |
| `MQTT_HOST` / `MQTT_PORT` | `127.0.0.1` / `1883` |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | vide |
| `MQTT_BASE_TOPIC` | `van/temperature` |
| `READ_INTERVAL_SECONDS` | `300` |
| `MISSED_CYCLES_BEFORE_OFFLINE` | `3` |

Une lecture DHT22 peut être réessayée cinq fois, avec deux secondes entre essais.
Les erreurs de mesure n'arrêtent pas le service. MQTT se reconnecte en arrière-plan.
SIGTERM/SIGINT libèrent le capteur et ferment MQTT.

Les topics de mesure restent `van/temperature/dht22` et ses champs scalaires,
avec le même JSON, QoS 1 et `retain`. `van/temperature/dht22/availability`
reste inchangé. Les diagnostics sont désormais réservés au GPIO :

```text
van/temperature/dht22/status   online / offline, retenu et Last Will
van/temperature/dht22/scan     bilan JSON du cycle DHT22
```

`van/temperature/status` et `van/temperature/scan` appartiennent désormais à la
passerelle Bluetooth. Cette séparation empêche les deux collecteurs de s'écraser
mutuellement leurs statuts. L'API ne dépend pas de ces topics de diagnostic.

Tests sans GPIO : installer `paho-mqtt` et `pytest`, puis exécuter
`python -m pytest -q` dans ce répertoire.
