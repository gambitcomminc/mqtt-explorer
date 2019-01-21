# mqtt-explorer
# MQTT Explorer - MQTT Subscriber client to explore topic hierarchies

## Overview

The MQTT Explorer subscriber client is designed to explore MQTT topics in your IoT Platform, show and sort statistics. It is useful if you want to analyze how much and what kind of information flows from publishers to the selected topic hierarchy. 

Commercial MQTT brokers have extensive global statistics in the $SYS topic, as detailed at
https://github.com/mqtt/mqtt.github.io/wiki/SYS-Topics
, but not per-topic statistics. Furthermore, there is no easy way to explore what's happening in real-time.

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

./mqtt-explorer.py --host iot.eclipse.org --topic '#' --qos 2

![screenshot](https://github.com/gambitcomminc/mqtt-explorer/blob/master/mqtt-explorer1.png)

The top area of the canvas displays status, such as what broker it is connected to, what topic hierarchy is subscribed to, and statistics about messages, topics, archiving and filtering.

By default, all topics are interesting only if they have published more than one message. This is because Will messages are only published once (see MQTT specs section 3.1.2.5). Interesting messages appear in the list above, uninteresting in the Filtered list below. You can make the lower list smaller by sliding the canvas separator between the 2 lists.

Rather than displaying topics in a flat space, they are listed in a hierarchy. Thus you can expand only the hierarchies of messages you are interested in. Clicking the blue triangle in front of a topic expands it, or you can select the topic by clicking on it, and press the Expand button to expand its entire hierarchy.

You can sort on any of the columns, eg. to find highest frequency topics, click on Msgs/s, or for highest bandwidth click on Bytes.

You can focus on certain topics by entering a regular expression in the Match topic(s) field. For example, ^edge matches all topics that start with edge. All others are hidden. 
