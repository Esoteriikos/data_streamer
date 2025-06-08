# python-processor/process_and_sink.py
import json
import time
import psycopg2
import sys
from kafka import KafkaConsumer
from kafka.errors import NoBrokersAvailable

# --- Configuration ---
KAFKA_BROKER = 'kafka:9092'
SOURCE_TOPIC = 'pokemon_raw'
CONSUMER_GROUP = 'pokemon-sink-final-v7' # Final, fresh consumer group

POSTGRES_HOST = 'postgres'
POSTGRES_DB = 'pokemon_db'
POSTGRES_USER = 'user'
POSTGRES_PASSWORD = 'password'

print("--- Starting Python Sink Processor (v7 - Data-Aware) ---", flush=True)

# --- Function to connect to Kafka (with retries) ---
def connect_to_kafka():
    for i in range(10):
        try:
            consumer = KafkaConsumer(
                SOURCE_TOPIC,
                bootstrap_servers=KAFKA_BROKER,
                auto_offset_reset='earliest',
                group_id=CONSUMER_GROUP,
                value_deserializer=lambda v: json.loads(v.decode('utf-8'))
            )
            print("Successfully connected to Kafka.", flush=True)
            return consumer
        except (NoBrokersAvailable, Exception) as e:
            print(f"Attempt {i+1}/10: Kafka connection failed: {e}. Retrying...", flush=True)
            time.sleep(5)
    return None

# --- Function to connect to PostgreSQL (with retries) ---
def connect_to_postgres():
    for i in range(10):
        try:
            conn = psycopg2.connect(host=POSTGRES_HOST, dbname=POSTGRES_DB, user=POSTGRES_USER, password=POSTGRES_PASSWORD)
            print("Successfully connected to PostgreSQL.", flush=True)
            return conn
        except psycopg2.OperationalError as e:
            print(f"Attempt {i+1}/10: Could not connect to PostgreSQL: {e}. Retrying...", flush=True)
            time.sleep(5)
    return None

# --- Main Execution ---
consumer = connect_to_kafka()
if not consumer:
    print("FATAL: Could not connect to Kafka. Exiting.", flush=True)
    sys.exit(1)

pg_conn = connect_to_postgres()
if not pg_conn:
    print("FATAL: Could not connect to PostgreSQL. Exiting.", flush=True)
    sys.exit(1)

# --- Create Table ---
with pg_conn.cursor() as cur:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS public.pokemon_details (
            id INT PRIMARY KEY,
            name VARCHAR(255),
            base_experience INT,
            height INT,
            weight INT,
            raw_data JSONB
        );
    """)
    pg_conn.commit()
    print("Table 'pokemon_details' is ready.", flush=True)

# --- Main Processing Loop ---
print("Consumer is running. Waiting for messages...", flush=True)
record_count = 0
try:
    for message in consumer:
        try:
            data = message.value
            if '_airbyte_data' not in data or not isinstance(data.get('_airbyte_data'), dict):
                print(f"Skipping non-Airbyte or malformed message at offset {message.offset}", flush=True)
                continue

            pokemon_data = data['_airbyte_data']
            pokemon_id = pokemon_data.get('id')
            name = pokemon_data.get('name')

            # THIS IS THE KEY LOGIC: Only process messages that have an 'id'
            if pokemon_id and name:
                with pg_conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO public.pokemon_details (id, name, base_experience, height, weight, raw_data) 
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            name = EXCLUDED.name,
                            base_experience = EXCLUDED.base_experience,
                            height = EXCLUDED.height,
                            weight = EXCLUDED.weight,
                            raw_data = EXCLUDED.raw_data;
                        """,
                        (pokemon_id, name, pokemon_data.get('base_experience'), pokemon_data.get('height'), pokemon_data.get('weight'), json.dumps(pokemon_data))
                    )
                pg_conn.commit()
                print(f"Successfully processed and inserted: {name} (ID: {pokemon_id})", flush=True)
                record_count += 1
            else:
                # This will skip the simple list records like {"name": "bulbasaur", "url": "..."}
                print(f"Skipping summary record at offset {message.offset}", flush=True)

        except Exception as e:
            print(f"CRITICAL ERROR processing message at offset {message.offset}: {e}", flush=True)
            pg_conn.rollback()

except KeyboardInterrupt:
    print("Shutdown signal received.", flush=True)
finally:
    print(f"--- Shutting down. Total records inserted in this run: {record_count} ---", flush=True)
    if pg_conn:
        pg_conn.close()
    if consumer:
        consumer.close()