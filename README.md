# ch.bus.temperature-mqtt

API HTTP de température alimentée par MQTT, historique du trajet en mémoire et
collecteur GPIO DHT22 pour Raspberry Pi. Toute acquisition Bluetooth est désormais
assurée par `ch.bus.bluetooth-mqtt`.

```text
Sondes BLE -> ch.bus.bluetooth-mqtt -> MQTT -> api/ (FastAPI)
DHT22 GPIO -> temperature/ ---------> MQTT -> historique / consommateurs
```

Les routes, les identifiants et les payloads de mesure sont conservés. Le
répertoire `temperature/` et son image restent disponibles, mais ils ne lisent
plus que le DHT22. Aucun conteneur de ce dépôt n'accède à Bluetooth ou à DBus.

| Identifiant MQTT/API | Famille | Collecteur |
| --- | --- | --- |
| `ca_pique` | Ruuvi | bluetooth-mqtt |
| `avalanche_toit` | SensorBlue/ThermoBeacon | bluetooth-mqtt |
| `fruit_storage` | Inkbird GATT | bluetooth-mqtt |
| `tete_used` | Inkbird GATT | bluetooth-mqtt |
| `dht22` | GPIO D4 | temperature/ |

La passerelle conserve les quatre adresses historiques comme valeurs par défaut.
Pour remplacer cette liste, transmettre le même tableau JSON `TEMPERATURE_SENSORS`
à la passerelle et à l'API : chaque entrée possède `name`, `address`, `protocol`
et éventuellement `gatt`. L'API ajoute automatiquement le DHT22. Sans cette
variable, ses cinq identifiants et ses réponses restent inchangés.

La procédure de bascule est dans le dépôt voisin :
[ch.bus.bluetooth-mqtt/MIGRATION.md](../ch.bus.bluetooth-mqtt/MIGRATION.md).
Arrêter l'ancien collecteur BLE avant le démarrage de la passerelle centrale.

## Construire et lancer le DHT22

```sh
docker build -t ch.bus.temperature-mqtt/temperature:latest ./temperature
docker build -t ch.bus.temperature-mqtt/api:latest ./api

docker run -d --restart=always --name temperature-mqtt \
  --net=host --device /dev/gpiomem:/dev/gpiomem \
  --stop-timeout 15 --env-file mqtt.env \
  ch.bus.temperature-mqtt/temperature:latest
```

Créer `mqtt.env` localement avec `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME` et
`MQTT_PASSWORD`. Ne pas y copier les clés Victron. Le collecteur ne demande que
l'accès GPIO correspondant au backend RPi.GPIO déjà utilisé. Consulter
[temperature/README.md](temperature/README.md) pour ses paramètres et limites.

Les diagnostics DHT22 passent à `van/temperature/dht22/status` et
`van/temperature/dht22/scan`. Le topic de mesure `van/temperature/dht22` et tous
ses champs restent identiques. `van/temperature/scan` conserve son schéma mais
ne compte plus le DHT22 ; les consommateurs de supervision doivent distinguer
les deux bilans. Les routes HTTP ne dépendent pas de ce changement.

## Préparer Mosquitto

Créer une fois le réseau utilisé par le fichier Compose :

```sh
docker network create van-mqtt-net
```

Créer le fichier de mots de passe depuis la racine du dépôt :

```sh
docker run --rm -it \
  -v "$PWD/docker/mosquitto/config:/mosquitto/config" \
  eclipse-mosquitto:2 \
  mosquitto_passwd -c /mosquitto/config/passwords victron
```

Le programme demande alors le mot de passe MQTT à attribuer à l'utilisateur
`victron`.

Lancer le broker :

```sh
docker compose -f docker/mosquitto/docker-compose.yml up -d
```

Le broker écoute sur le port `1883` de l'hôte et refuse les connexions
anonymes.

## Lancer l'API

L'API s'abonne par défaut à `van/temperature/+`. Elle ignore les topics de
statut, conserve le dernier paquet JSON complet de chacun des cinq capteurs et
ajoute chaque paquet reçu à l'historique du trajet. Cet historique reste en
mémoire et repart donc vide à chaque redémarrage du conteneur API.

```sh
docker run -d \
  --restart=always \
  --name temperature-api \
  --net=host \
  -e MQTT_HOST="127.0.0.1" \
  -e MQTT_PORT="1883" \
  -e MQTT_USERNAME="victron" \
  -e MQTT_PASSWORD="CHANGE_ME_MQTT_PASSWORD" \
  -e MQTT_BASE_TOPIC="van/temperature" \
  -e API_PORT="8013" \
  ch.bus.temperature-mqtt/api:latest
```

### Variables de l'API

| Variable | Défaut | Description |
| --- | --- | --- |
| `MQTT_HOST` | `127.0.0.1` | Adresse du broker |
| `MQTT_PORT` | `1883` | Port du broker |
| `MQTT_USERNAME` | `victron` | Utilisateur MQTT |
| `MQTT_PASSWORD` | `change-me` | Mot de passe MQTT |
| `MQTT_BASE_TOPIC` | `van/temperature` | Racine des topics |
| `MQTT_TOPIC` | `van/temperature/+` | Filtre MQTT, surcharge facultative |
| `API_PORT` | `8013` | Port HTTP |
| `LOG_LEVEL` | `INFO` | Niveau des logs Python |

Aucun volume ni service de base de données n'est nécessaire pour l'historique.

## Topics MQTT

Les paquets JSON complets sont publiés en QoS 1 avec l'option `retain` :

```text
van/temperature/ca_pique
van/temperature/avalanche_toit
van/temperature/fruit_storage
van/temperature/tete_used
van/temperature/dht22
```

Exemple de paquet :

```json
{
  "timestamp": "2026-06-22T20:15:00.000000+00:00",
  "name": "Fruit Storage",
  "address": "49:22:11:08:18:64",
  "protocol": "inkbird",
  "rssi": -61,
  "model": "Inkbird IBS-TH/IBS-TH2",
  "temperature": 7.42,
  "humidity": 71.35,
  "battery": 86
}
```

Chaque champ scalaire est également publié séparément :

```text
van/temperature/fruit_storage/temperature
van/temperature/fruit_storage/humidity
van/temperature/fruit_storage/battery
van/temperature/fruit_storage/rssi
```

Topics de supervision :

| Topic | Contenu |
| --- | --- |
| `van/temperature/status` | alias de statut du collecteur BLE centralisé |
| `van/temperature/scan` | bilan JSON des sondes BLE uniquement |
| `van/temperature/<capteur>/availability` | disponibilité du capteur |
| `van/bluetooth/status` | statut et Last Will de la passerelle BLE |
| `van/temperature/dht22/status` | statut et Last Will DHT22 |
| `van/temperature/dht22/scan` | bilan JSON du DHT22 |

Observer toutes les publications :

```sh
docker exec -it van-mqtt mosquitto_sub \
  -u victron \
  -P 'CHANGE_ME_MQTT_PASSWORD' \
  -t 'van/temperature/#' -v
```

## API HTTP

### État du service

```text
GET /api/health
```

```sh
curl http://127.0.0.1:8013/api/health
```

Exemple :

```json
{
  "status": "ok",
  "mqtt_connected": true,
  "last_message_timestamp": "2026-06-22T20:15:01.000000+00:00",
  "sensor_count": 5,
  "expected_sensor_count": 5
}
```

### Tous les capteurs

```text
GET /api/sensors
GET /api/metrics
```

`/api/metrics` est un alias conservé pour les clients existants.

```sh
curl http://127.0.0.1:8013/api/sensors
```

La réponse contient `sensor_count`, `missing_sensors` et un objet `sensors`
indexé par identifiant. Tant qu'aucune donnée MQTT n'a été reçue, l'API répond
avec le statut HTTP `503`.

### Un seul capteur

```text
GET /api/sensors/{sensor_id}
```

Exemples :

```sh
curl http://127.0.0.1:8013/api/sensors/ca_pique
curl http://127.0.0.1:8013/api/sensors/avalanche_toit
curl http://127.0.0.1:8013/api/sensors/fruit_storage
curl http://127.0.0.1:8013/api/sensors/tete_used
curl http://127.0.0.1:8013/api/sensors/dht22
```

Un identifiant inconnu retourne `404`. Un capteur connu qui n'a encore envoyé
aucune mesure retourne `503`.

### Historique du trajet

```text
GET /api/history
GET /api/history?sensor_id=ca_pique
GET /api/history/{sensor_id}
```

Chaque mesure MQTT valide est ajoutée dans l'ordre de réception. La réponse
contient `started_at`, `reading_count` et la liste `readings`. Chaque élément
reprend le paquet original, complété avec `sensor_id` et `received_at`.
Les anciennes valeurs `retain` rejouées par Mosquitto au démarrage restent
visibles dans `/api/sensors`, mais ne sont pas ajoutées au nouveau trajet.

```sh
curl http://127.0.0.1:8013/api/history
curl http://127.0.0.1:8013/api/history/dht22
```

L'historique est volontairement éphémère : arrêter puis recréer ou redémarrer
le conteneur `temperature-api` commence un nouveau trajet.

La documentation OpenAPI interactive est disponible sur :

```text
http://<adresse-du-raspberry>:8013/docs
```

## Vérification et tests

```sh
docker logs --tail 100 temperature-mqtt
docker logs --tail 100 temperature-api
python -m pip install -r requirements-test.txt
python -m pytest -q temperature
python -m pytest -q api
```

Le matériel GPIO/Bluetooth n'est pas nécessaire aux tests. Les tests BLE sont
maintenant dans `ch.bus.bluetooth-mqtt`. Les paquets retenus alimentent toujours
la dernière valeur de l'API sans être ajoutés à l'historique du nouveau trajet.

Ne pas enregistrer de vrais mots de passe dans Git. Les fichiers `.env`, `*.env`
et `docker/mosquitto/config/passwords` restent locaux. Ne pas démarrer un second
broker si le Mosquitto existant est déjà partagé avec Victron et la passerelle.
