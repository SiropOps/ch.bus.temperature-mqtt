# ch.bus.temperature-mqtt

Passerelle Docker pour relever des capteurs de température en Bluetooth Low
Energy et un DHT22 câblé sur D4 sur un Raspberry Pi, publier leurs mesures dans
MQTT et les exposer avec une API HTTP.

## Architecture

```text
Capteurs BLE + DHT22 sur D4
    |
    v
temperature (écoute BLE, lecture DHT22, publication toutes les 5 minutes)
    |
    v
Mosquitto MQTT  <----  api (FastAPI, port 8013)
    |
    +---- Home Assistant / Node-RED / Grafana / Inkplate
```

Le collecteur écoute passivement les annonces BLE. Il n'établit pas de
connexion permanente avec les capteurs, ce qui économise leurs piles.

## Capteurs configurés

| Identifiant MQTT/API | Nom | Adresse MAC | Famille |
| --- | --- | --- | --- |
| `ca_pique` | Ça pique | `E3:EE:E4:14:FA:B0` | Ruuvi |
| `avalanche_toit` | Avalanche Toit | `9D:88:00:00:02:2C` | SensorBlue/ThermoBeacon |
| `fruit_storage` | Fruit Storage | `49:22:11:08:18:64` | Engbird Inkbird |
| `tete_used` | Tête used | `49:22:09:05:14:A1` | Engbird Inkbird |
| `dht22` | DHT22 | `GPIO D4` | DHT22 filaire |

La configuration et les décodeurs se trouvent dans
`temperature/app.py`.

## Contenu du projet

```text
temperature/              collecteur BLE vers MQTT
api/                      API HTTP abonnée aux mesures MQTT
docker/mosquitto/         broker MQTT et sa configuration
```

## Prérequis

- Raspberry Pi avec Bluetooth activé ;
- capteur DHT22 connecté à D4 (GPIO 4) ;
- Docker et Docker Compose v2 ;
- accès aux annonces BLE des quatre capteurs ;
- ports `1883` (MQTT) et `8013` (API) disponibles.

## 1. Préparer Mosquitto

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

## 2. Construire les images

Depuis la racine du dépôt :

```sh
docker build -t ch.bus.temperature-mqtt/temperature:latest ./temperature
docker build -t ch.bus.temperature-mqtt/api:latest ./api
```

## 3. Lancer le collecteur BLE

Le mode réseau hôte, le mode privilégié et le montage DBus permettent au
conteneur d'utiliser l'adaptateur Bluetooth du Raspberry Pi.

```sh
docker run -d \
  --restart=always \
  --name temperature-mqtt \
  --net=host \
  --privileged \
  -v /var/run/dbus:/var/run/dbus \
  -e MQTT_HOST="127.0.0.1" \
  -e MQTT_PORT="1883" \
  -e MQTT_USERNAME="victron" \
  -e MQTT_PASSWORD="CHANGE_ME_MQTT_PASSWORD" \
  -e MQTT_BASE_TOPIC="van/temperature" \
  -e READ_INTERVAL_SECONDS="300" \
  -e SCAN_TIMEOUT_SECONDS="45" \
  -e MISSED_CYCLES_BEFORE_OFFLINE="3" \
  ch.bus.temperature-mqtt/temperature:latest
```

Le scanner BLE reste actif entre les publications afin de capter les sondes dont
le signal est faible. Au démarrage, une première publication a lieu dès que les
quatre capteurs ont répondu, ou après `SCAN_TIMEOUT_SECONDS`. Ensuite, les mesures
les plus récentes sont publiées toutes les `READ_INTERVAL_SECONDS` secondes.

### Variables du collecteur

| Variable | Défaut | Description |
| --- | --- | --- |
| `MQTT_HOST` | `127.0.0.1` | Adresse du broker |
| `MQTT_PORT` | `1883` | Port du broker |
| `MQTT_USERNAME` | vide | Utilisateur MQTT |
| `MQTT_PASSWORD` | vide | Mot de passe MQTT |
| `MQTT_BASE_TOPIC` | `van/temperature` | Racine des topics |
| `READ_INTERVAL_SECONDS` | `300` | Période entre deux publications |
| `SCAN_TIMEOUT_SECONDS` | `45` | Attente maximale du premier relevé au démarrage |
| `MISSED_CYCLES_BEFORE_OFFLINE` | `3` | Cycles manqués avant de publier `offline` |

## 4. Lancer l'API

L'API s'abonne par défaut à `van/temperature/+`. Elle ignore les topics de
statut et conserve en mémoire le dernier paquet JSON complet de chacun des
cinq capteurs.

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
| `van/temperature/status` | `online` ou `offline` pour le collecteur |
| `van/temperature/scan` | bilan JSON du dernier scan |
| `van/temperature/<capteur>/availability` | présence au dernier scan |

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

La documentation OpenAPI interactive est disponible sur :

```text
http://<adresse-du-raspberry>:8013/docs
```

## Vérification et dépannage

Afficher les logs :

```sh
docker logs -f van-mqtt
docker logs -f temperature-mqtt
docker logs -f temperature-api
```

Vérifier le Bluetooth sur l'hôte :

```sh
bluetoothctl show
bluetoothctl scan on
```

Si aucun capteur n'est trouvé :

- vérifier que le Bluetooth est actif sur le Raspberry Pi ;
- vérifier le montage `/var/run/dbus` et l'option `--privileged` ;
- éloigner le Raspberry Pi des sources d'interférences USB 3/Wi-Fi ;
- comparer les adresses détectées avec celles de `temperature/app.py` ;
- augmenter temporairement `SCAN_TIMEOUT_SECONDS`.

## Sécurité

Ne pas enregistrer de vrais mots de passe dans Git. Le fichier
`docker/mosquitto/config/passwords`, les fichiers `.env` et les secrets de
déploiement doivent rester locaux. Pour un broker accessible depuis un autre
réseau, ajouter TLS et limiter le port `1883` avec le pare-feu.
