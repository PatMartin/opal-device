#!/bin/bash

# Stop any running opal-device processes
opal-device service stop

# Start opal-device in the background
opal-device service start

# Wait a moment for the service to start
sleep 2

opal-device --help
