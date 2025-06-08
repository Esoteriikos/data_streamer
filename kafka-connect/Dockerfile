# Use the reliable Debezium image as our base
FROM debezium/connect:2.5

# Copy our connectors and drivers into the plugin path
COPY postgresql-jdbc.jar /kafka/connect/