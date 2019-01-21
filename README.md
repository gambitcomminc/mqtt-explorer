# mqtt-explorer
# MQTT Explorer - MQTT Subscriber client to explore topic hierarchies

## Overview

The MQTT Explorer subscriber client is designed to explore MQTT topics in your IoT Platform, show and sort statistics. It is useful if you want to analyze how much and what kind of information flows from publishers to the selected topic hierarchy. 

Commercial MQTT brokers have extensive global statistics in the $SYS topic, as detailed here, but not per-topic statistics. Furthermore, there is no easy way to explore what's happening in real-time.

 Relevant questions are:

    What topics are being published to?

    Which topics have the most messages?

    Which topics have the highest bandwidth?

    Are there misbehaving sensors (cry-babies) that need throttling?

    Which topics receive certain payloads? 

The existing simple open-source mqtt-stats subscriber client already displays per-topic statistics, eg. like mqtt-spy or MQTTLens, but more. It uses GTK to present a GUI. This utility allows you to analyze quantitatively the published topics underneath a wildcard topic and answer such questions as "which topic generates the most messages?" and "which topic generates the most traffic?". You can sort by messages/second to get the most active topics. 

The MQTT Explorer improves on this to explore interesting topics. It allows to filter topics, hides uninteresting topics, and allows to archive payloads for later replay by MIMIC MQTT Simulator . 

## Installation / Requirements

This python package requires

* PyGTK https://python-gtk-3-tutorial.readthedocs.io/en/latest/
* Eclipse Paho MQTT client API https://www.eclipse.org/paho/clients/python/docs/

## Usage

Example usage:

./mqtt-stats.py --host iot.eclipse.org --topic '#' --qos 2

![screenshot](https://github.com/gambitcomminc/mqtt-explorer/blob/master/mqtt-explorer1.png)

If you use File->New it zeros out the collected topics, and will display the active topics from now on. This is because the broker publishes received "will" messages on all topics first. Most of those topics may no longer be active.


File -> Save dumps the topic statistics to the file dump.lst. 
