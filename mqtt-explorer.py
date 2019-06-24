#!/usr/bin/env python

##############################################################################
## Copyright (c) 2018 by Gambit Communications, Inc.
## All Rights Reserved
##############################################################################

from __future__ import print_function

import os 
import errno 
import getopt
import sys
import socket
import time
import datetime
from datetime import date
import logging
import threading
import binascii
import re
import pickle
import webbrowser
import ssl

# debug.setLogger(debug.Debug('all'))

formatting = '%(levelname)s %(asctime)s (%(filename)s-%(lineno)d) - %(message)s'
logging.basicConfig(level=logging.DEBUG, format=formatting, )

logging.basicConfig(level=logging.ERROR, format=formatting, )


import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')

try:
    from gi.repository import Gtk
    from gi.repository import Gdk
    from gi.repository import GObject
    from gi.repository import Pango as pango
except:
    logging.error ("require Gtk")
    sys.exit(1)

import paho.mqtt.client as mqtt

import json


###########################################################################


###########################################################################
debug = False
if debug:
    from colorlog import ColoredFormatter
    def setup_logger():
        """Return a logger with a default ColoredFormatter."""
        formatter = ColoredFormatter(
            "(%(threadName)-9s) %(log_color)s%(levelname)-8s%(reset)s %(message_log_color)s%(message)s",
            datefmt=None,
            reset=True,
            log_colors={
                'DEBUG': 'cyan',
                'INFO': 'green',
                'WARNING': 'yellow',
                'ERROR': 'red',
                'CRITICAL': 'red',
            },
            secondary_log_colors={
                'message': {
                    'ERROR': 'red',
                    'CRITICAL': 'red',
                    'DEBUG': 'yellow'
                }
            },
            style='%'
        )

        logger = logging.getLogger(__name__)
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        return logger

    # Create a player
    logger = setup_logger()

    from functools import wraps
    def trace(func):
        """Tracing wrapper to log when function enter/exit happens.
        :param func: Function to wrap
        :type func: callable
        """
        @wraps(func)
        def wrapper(*args, **kwargs):
            logger.debug('Start {!r}'. format(func.__name__))
            result = func(*args, **kwargs)
            logger.debug('End {!r}'. format(func.__name__))
            return result
        return wrapper

else:

    from functools import wraps
    def trace(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            return result
        return wrapper


###########################################################################
class _IdleObject(GObject.GObject):
    """
    Override GObject.GObject to always emit signals in the main thread
    by emmitting on an idle handler
    """

    @trace
    def __init__(self):
        GObject.GObject.__init__(self)

    @trace
    def emit(self, *args):
        GObject.idle_add(GObject.GObject.emit, self, *args)


###########################################################################
class _UpdateThread(threading.Thread, _IdleObject):
    """
    Cancellable thread which uses gobject signals to return information
    to the GUI.
    """
    __gsignals__ = {
        "completed": (
            GObject.SignalFlags.RUN_LAST, None, []),
        "progress": (
            GObject.SignalFlags.RUN_LAST, None, [
                GObject.TYPE_FLOAT])  # percent complete
    }

    @trace
    def __init__(self, parent):
        threading.Thread.__init__(self, target=self.update_main, name="Update Thread")
        _IdleObject.__init__(self)
        self.cancelled = False

    # main thread for the update thread
    # this thread periodically checks for any changes
    # and initiates updates
    @trace
    def update_main(self):
        while True:
            # wake up every second for response
            time.sleep (1.999)

            if main.is_stopped:
                break

            if main.is_paused:
                continue

            # but only do the work every N seconds
#			count += 1
#			if count < self.poller.poll_interval:
#				continue
#			count = 0

            logging.debug ("update_cycle start")
            self.update_cycle()
            logging.debug ("update_cycle completed")
            self.emit("completed")

        logging.debug ("done update_main")

    # run a poll cycle
    @trace
    def update_cycle(self):
        # logging.debug ("done update_cycle " )
        return


###########################################################################
# Topic statistics
class Topic():
    def __init__(self, number, topic, bytes, last_time, last_payload):
        self.topic = topic
        self.number = number
        self.count = 1;		# seen at least once
        self.bytes = bytes
        self.last_time = last_time
        self.last_payload = last_payload
        self.rowref = None
	self.is_filtered = True	# start out filtered
	self.is_custom = False	# not customized, ie. under any of the paths
	                        # in main.filtered_topics
	self.is_archived = False	# not archived, ie. under any of the
				# paths in main.archived_topics

    def dump(self, outfile):
#        try:
#            topicstr = self.topic.decode('utf-8')
#        except:
        topicstr = self.topic
	if self.is_filtered:
	    is_filtered = 1
	else:
	    is_filtered = 0
        print ('%6d %20s %6d %6d %s %d' % (self.number, topicstr, self.count, self.bytes, self.last_payload, is_filtered), file=outfile)

    def archive(self):
        filename = 'archived/' + self.topic + '/' + time.strftime ('%Y-%m-%d-%H:%M:%S', time.localtime (self.last_time)) + str (self.last_time - int(self.last_time)) 
	if not os.path.exists(os.path.dirname(filename)):
            try:
                os.makedirs(os.path.dirname(filename))
	    except OSError as exc: # Guard against race condition
	        if exc.errno != errno.EEXIST:
	            raise

        with open(filename, "w") as f:
	    f.write(self.last_payload)
	    f.close()

###########################################################################
# tree model update

###########################################################################
# MQTT subscriber code
# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
    logging.debug ("MQTT client connected with result code "+str(rc))

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe(main.topic, main.qos)

# The callback for when a PUBLISH message is received from the server.
# updates metrics for later GUI display
def on_message(client, userdata, msg):
    if main.do_clear_stats:
        main.messages_received = 0
        main.last_received = 0
        main.num_topics = 0
        main.topics = dict()
	main.changed = dict()		# a small subset of topics that is changed
	main.last_changed = dict()	# the changed in the last iteration
					# GUI needs this to 0 out some values
        main.do_clear_stats = False
        main.clear_store = True

    bytes = len (msg.payload)
    now = time.time()

#    logging.debug (msg.topic + ' ' + str(bytes) + ' ' + str(msg.payload))

    main.messages_received += 1

    # if not already there, add to set of topics detected
    topicstr = msg.topic
    if topicstr not in main.topics:
        main.num_topics += 1
        newtopic = Topic(main.num_topics, topicstr, bytes, now, msg.payload)
        main.topics[topicstr] = newtopic
	thistopic = newtopic
    else:
        existingtopic = main.topics[topicstr]
        existingtopic.count += 1
        existingtopic.bytes += bytes
        existingtopic.last_time = now
        existingtopic.last_payload = msg.payload
	thistopic = existingtopic

    if thistopic.number not in main.changed:
	main.changed[thistopic.number] = msg.topic

    if thistopic.is_archived:
        thistopic.archive()
	main.archived_count += 1
    return


def on_disconnect(client, userdata, rc):

    if rc != 0:
        logging.error ("unexpected disconnect: " + str(rc))

def subscriber_client():
    client = mqtt.Client(main.client_id)
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    if (main.is_tls):
#		logging.debug ("cafile " + main.cafile)
	client.tls_set(ca_certs=main.cafile, certfile=main.certfile, keyfile=main.keyfile, tls_version=ssl.PROTOCOL_SSLv23, cert_reqs=ssl.CERT_NONE)
	client.tls_insecure_set(True)

    try:
        client.connect(main.host_ip, main.port_num, 60)
    except:
        logging.error ("cannot connect")
        sys.exit(1)

    client.loop_start()


###########################################################################
class MyApp:
    def __init__(self):
        self.host_ip = None
        self.port_num = None
        self.verbose = False
	self.client_id = None
        self.topic = '#'
        self.qos = 0
	self.is_tls = False
	self.cafile = ""
	self.certfile = None
	self.keyfile = None

        self.is_stopped = False
        self.is_paused = False

        self.messages_received = 0
        self.last_received = 0
        self.num_topics = 0
        self.topics = dict()
	self.changed = dict()
	self.last_changed = dict()
        self.do_clear_stats = False
        self.clear_store = False
        self.do_pause = False			# pause button state
        self.do_freeze = False			# freeze button state
	self.sort_column = 0
	self.sort_order = Gtk.SortType.ASCENDING

	self.interesting_count = 0
	self.filtered_count = 0
	self.filtered_topics = set()	# we need to keep the set of filtered
					# topic paths in addition to individual
					# topics

	self.archived_count = 0		# count of archived messages
	self.archived_topics = set()	# we need to keep the set of archived
					# topic paths

	# load them from persistency
	if os.path.isfile('filtered.cfg'):
	    infile = open('filtered.cfg', 'rb') # rb needed for python 3 support
	    self.filtered_topics = pickle.Unpickler (infile).load()
	    infile.close()
	    logging.debug ('loaded from filtered.cfg ' + str(len(self.filtered_topics)) + ' filtered topic trees')

	    # for key in self.filtered_topics:
	    # 	logging.debug ('filtering topic ' + key)

	self.now = time.time()
	self.last_time = 0

	self.excluded = 0
	self.shown_topic_numbers = set()	# topic numbers shown
	self.matchprog = None		# regular expression for match filter

    def usage(self):
        print ("Usage: mqtt-explorer.py")
        print ("\t[-h|--host host]   broker to connect to; default localhost")
        print ("\t[-p|--port port]   port to connect to; default port 1883")
	print ("\t[-i|--id clientid] client ID; default random")
        print ("\t[-t|--topic topic] topic; default #")
        print ("\t[-q|--qos qos]     QoS; default 0")
        print ("\t[-v|--verbose]     verbose output")
	print ("")
	print ("\t[-T|--tls]         use TLS")
	print ("\t[-c|--cafile]      certificate authority file for TLS")
	print ("\t[-C|--certfile]    client certificate file for TLS")
	print ("\t[-K|--keyfile]     client private key file for TLS")
        return

    def start(self):
        self.command_line()

	self.load_styles()

        self.show_gui()
        # from now on GUI is expected to be up

        subscriber_client ()

        self.update_thread = _UpdateThread(self)
        self.update_thread.connect("completed", self.completed_cb)
        self.update_thread.start()

        Gtk.main()

    ###############################
    def command_line(self):
        try:
	    opts, args = getopt.getopt(sys.argv[1:], "h:p:i:t:q:vTc:C:K:", ["host=", "port=", "id=", "topic=", "qos=", "verbose", "tls", "cafile=", "certfile=", "keyfile="])
        except getopt.GetoptError as err:
            # print help information and exit:
            logging.error (str(err)) # will print something like "option -a not recognized"
            self.usage()
            sys.exit(1)

        for o, a in opts:
            if o in ("-v", "--verbose"):
                self.verbose = True
            elif o in ("-h", "--host"):
                self.host_ip = a
            elif o in ("-p", "--port"):
                self.port_num = a
	    elif o in ("-i", "--id"):
		self.client_id = a
            elif o in ("-t", "--topic"):
                self.topic = a
            elif o in ("-q", "--qos"):
                self.qos = int(a)
	    elif o in ("-T", "--tls"):
		self.is_tls = True
	    elif o in ("-c", "--cafile"):
		self.cafile = a
	    elif o in ("-C", "--certfile"):
		self.certfile = a
	    elif o in ("-K", "--keyfile"):
		self.keyfile = a
            else:
                assert False, "unhandled option"

        if self.host_ip == None:
            self.host_ip = "127.0.0.1"

        if self.port_num == None:
            self.port_num = 1883

    ###############################
    # filter out messages with less than 2 messages
    def message_filter_func(self, model, iter, data):
        return model[iter][2] > 1

    ###############################
    def load_styles(self):
	# CSS styles for GTK objects
	style_provider = Gtk.CssProvider()
        dir_path = os.path.dirname(os.path.realpath(__file__))
        css_path = dir_path+"/mqtt-explorer.css"
	try:
	    css = open(css_path, 'rb') # rb needed for python 3 support
	except:
	    logging.warning ("no CSS file " + css_path + ", ignoring...")
	    return
	css_data = css.read()
	css.close()

	style_provider.load_from_data(css_data)

	Gtk.StyleContext.add_provider_for_screen(
	    Gdk.Screen.get_default(),
	    style_provider,     
	    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
	)

    ###############################
    def show_gui(self):
        self.builder = Gtk.Builder()
        dir_path = os.path.dirname(os.path.realpath(__file__))
        glade_path = dir_path+'/mqtt-explorer.glade'
        self.builder.add_from_file(glade_path)
        self.builder.connect_signals(Handler())

	# build the GUI
        self.window = self.builder.get_object('mainWindow')

        # dialogs
        # Help->About
        self.aboutdialog = self.builder.get_object('aboutdialog1')

        # File->New

        self.errordialog = self.builder.get_object('errordialog')

	# topicstore menu UWE DOES NOT WORK
	# topmenu = self.builder.get_object('topMenu')
	#self.topicstoremenu = gtk.Menu()
	#menu_item = gtk.MenuItem("A menu item")
	#menu_item.show()
	#self.topicstoremenu.append(menu_item)

        # status bar
        self.statusbar = self.builder.get_object('statusmessage')
        self.context_id = self.statusbar.get_context_id('status')
        self.clients = self.builder.get_object('clients')
        self.clients_context = self.statusbar.get_context_id('clients')
        self.freevm = self.builder.get_object('freevm')
        self.freevm_context = self.statusbar.get_context_id('freevm')
        self.activity_meter = self.builder.get_object('activitymeter')

	# toolbar
	# buttons that enable when item selected
	self.filter_button = self.builder.get_object('filter_button')
	self.filter_button.set_sensitive (False)
	self.copy_button = self.builder.get_object('copy_button')
	self.copy_button.set_sensitive (False)
	self.expand_button = self.builder.get_object('expand_button')
	self.expand_button.set_sensitive (False)
	self.archive_button = self.builder.get_object('archive_button')
	self.archive_button.set_sensitive (False)

        # the first titlelabel
        self.titlelabel = self.builder.get_object('titlelabel')
        self.titlelabel.set_text('MQTT Topic Explorer')

        self.infolabel1 = self.builder.get_object('infolabel1')
        self.infolabel1.set_text('MQTT Broker: ' + self.host_ip + '\nStarted: ' + time.ctime (time.time()))

        self.infolabel2 = self.builder.get_object('infolabel2')
        self.infolabel2.set_text('Subscribed Topic: ' + main.topic)

        self.infolabel3 = self.builder.get_object('infolabel3')
        self.infolabel3.set_text('')

        self.infolabel4 = self.builder.get_object('infolabel4')
        self.infolabel4.set_text('')

        self.infolabel5 = self.builder.get_object('infolabel5')
        self.infolabel5.set_text('')

        self.infolabel6 = self.builder.get_object('infolabel6')
        self.infolabel6.set_text('')

        self.infolabel7 = self.builder.get_object('infolabel7')
        self.infolabel7.set_text('Interesting: ' + str(main.interesting_count))

	self.infolabel9 = self.builder.get_object('infolabel9')
	self.infolabel9.set_text('')

	self.infolabel13 = self.builder.get_object('infolabel13')
	self.infolabel13.set_text('')

	self.infolabel14 = self.builder.get_object('infolabel14')
	self.infolabel14.set_text('')

        # the topicstore containing the interesting topics
        self.topicstore = self.builder.get_object('topicstore')
        self.treemodelsort = self.builder.get_object('treemodelsort1')

        # filtering
	self.treefilter = self.builder.get_object('treemodelfilter1')
	self.treefilter.set_visible_func(self.match_func, data=None)

        # treeview = Gtk.TreeView(self.message_filter)
        treeview = self.builder.get_object('treeview1')
        self.treeview = treeview
        tvcolumn = Gtk.TreeViewColumn('No')
        treeview.append_column(tvcolumn)
        numbercell = Gtk.CellRendererText(xalign=1.0)
        numbercell.set_property('ellipsize', pango.EllipsizeMode.END)
        tvcolumn.pack_start(numbercell, True)
        tvcolumn.add_attribute(numbercell, 'text', 0)
        tvcolumn.set_sort_column_id(0)
        tvcolumn.set_resizable(True)

        tvcolumn = Gtk.TreeViewColumn('Topic')
        treeview.append_column(tvcolumn)
        stringcell = Gtk.CellRendererText()
        stringcell.set_property('ellipsize', pango.EllipsizeMode.END)
        tvcolumn.pack_start(stringcell, True)
        tvcolumn.add_attribute(stringcell, 'text', 1)
        tvcolumn.set_sort_column_id(1)
        tvcolumn.set_resizable(True)
        #tvcolumn.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
        #tvcolumn.set_min_width(20)
        #tvcolumn.set_fixed_width(80)
        #tvcolumn.set_expand(True)

        tvcolumn = Gtk.TreeViewColumn('Msgs')
        treeview.append_column(tvcolumn)
        tvcolumn.pack_start(numbercell, True)
        tvcolumn.add_attribute(numbercell, 'text', 2)
        tvcolumn.set_sort_column_id(2)
        tvcolumn.set_resizable(True)

        tvcolumn = Gtk.TreeViewColumn('Msgs/s')
        treeview.append_column(tvcolumn)
        tvcolumn.pack_start(numbercell, True)
        tvcolumn.add_attribute(numbercell, 'text', 3)
        tvcolumn.set_sort_column_id(3)
        tvcolumn.set_resizable(True)

        tvcolumn = Gtk.TreeViewColumn('Bytes')
        treeview.append_column(tvcolumn)
        tvcolumn.pack_start(numbercell, True)
        tvcolumn.add_attribute(numbercell, 'text', 4)
        tvcolumn.set_sort_column_id(4)
        tvcolumn.set_resizable(True)

        tvcolumn = Gtk.TreeViewColumn('Time')
        treeview.append_column(tvcolumn)
        tvcolumn.pack_start(stringcell, True)
        tvcolumn.add_attribute(stringcell, 'text', 5)
        tvcolumn.set_sort_column_id(5)
        tvcolumn.set_resizable(True)

        tvcolumn = Gtk.TreeViewColumn('Last Payload')
        treeview.append_column(tvcolumn)
        tvcolumn.pack_start(stringcell, True)
        tvcolumn.add_attribute(stringcell, 'text', 6)
        tvcolumn.set_sort_column_id(6)
        tvcolumn.set_resizable(True)

	# the 7th column in the model is the raw topic name, is not displayed
	# the 8th column in the model is the visibility flag, is not displayed

	selection = treeview.get_selection()
#	selection.set_select_function(self.on_treeview_select_func, None)
	selection.set_mode(Gtk.SelectionMode.MULTIPLE)
	selection.connect('changed', self.on_topicstore_selection_changed)

        self.infolabel8 = self.builder.get_object('infolabel8')
        self.infolabel8.set_text('Filtered: ' + str(main.filtered_count))

        # the filteredstore containing the uninteresting topics
        self.filteredstore = self.builder.get_object('filtered')

        filteredview = self.builder.get_object('treeview2')
        self.filteredview = filteredview
        tvcolumn = Gtk.TreeViewColumn('No')
        filteredview.append_column(tvcolumn)
        numbercell = Gtk.CellRendererText(xalign=1.0)
        numbercell.set_property('ellipsize', pango.EllipsizeMode.END)
        tvcolumn.pack_start(numbercell, True)
        tvcolumn.add_attribute(numbercell, 'text', 0)
        tvcolumn.set_sort_column_id(0)
        tvcolumn.set_resizable(True)

        tvcolumn = Gtk.TreeViewColumn('Topic')
        filteredview.append_column(tvcolumn)
        stringcell = Gtk.CellRendererText()
        stringcell.set_property('ellipsize', pango.EllipsizeMode.END)
        tvcolumn.pack_start(stringcell, True)
        tvcolumn.add_attribute(stringcell, 'text', 1)
        tvcolumn.set_sort_column_id(1)
        tvcolumn.set_resizable(True)
        #tvcolumn.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
        #tvcolumn.set_min_width(20)
        #tvcolumn.set_fixed_width(80)
        #tvcolumn.set_expand(True)

        tvcolumn = Gtk.TreeViewColumn('Msgs')
        filteredview.append_column(tvcolumn)
        tvcolumn.pack_start(numbercell, True)
        tvcolumn.add_attribute(numbercell, 'text', 2)
        tvcolumn.set_sort_column_id(2)
        tvcolumn.set_resizable(True)

        tvcolumn = Gtk.TreeViewColumn('Msgs/s')
        filteredview.append_column(tvcolumn)
        tvcolumn.pack_start(numbercell, True)
        tvcolumn.add_attribute(numbercell, 'text', 3)
        tvcolumn.set_sort_column_id(3)
        tvcolumn.set_resizable(True)

        tvcolumn = Gtk.TreeViewColumn('Bytes')
        filteredview.append_column(tvcolumn)
        tvcolumn.pack_start(numbercell, True)
        tvcolumn.add_attribute(numbercell, 'text', 4)
        tvcolumn.set_sort_column_id(4)
        tvcolumn.set_resizable(True)

        tvcolumn = Gtk.TreeViewColumn('Time')
        filteredview.append_column(tvcolumn)
        tvcolumn.pack_start(stringcell, True)
        tvcolumn.add_attribute(stringcell, 'text', 5)
        tvcolumn.set_sort_column_id(5)
        tvcolumn.set_resizable(True)

        tvcolumn = Gtk.TreeViewColumn('Last Payload')
        filteredview.append_column(tvcolumn)
        tvcolumn.pack_start(stringcell, True)
        tvcolumn.add_attribute(stringcell, 'text', 6)
        tvcolumn.set_sort_column_id(6)
        tvcolumn.set_resizable(True)

        self.window.show_all()

    # recursively traverse the tree, and set the visible flag
    # if do_increment is True, then we need to increment counters
    @trace
    def recursive_topicstore_match(self, model, treeiter, do_increment):
        if treeiter is None:
	    logging.error ('recursive_topicstore_match treeiter is None, should never happen')
	    return True

        citer = model.iter_children(treeiter)
	# no more children, match the leaf
	if citer is None:
	    if not self.topicstore_match_leaf (model, treeiter):
	        if do_increment:
	            self.excluded += 1
		    # logging.debug ('=========== recursive_topicstore_match excluded = ' + str(main.excluded))
		return False
	    number = model[treeiter][0]
	    if number not in self.shown_topic_numbers:
	    	# logging.debug ('shown_topic_numbers <--' + str(number) + ' --> len ' + str(len(self.shown_topic_numbers)))
		self.shown_topic_numbers.add(number)
	    return True

	# check all the children, if any of them is visible, this is too
	visible = False
	while citer is not None:
	    if self.recursive_topicstore_match (model, citer, do_increment):
		visible = True
	    citer = model.iter_next(citer)

	if visible:
	    # logging.debug ('recursive_topicstore_match ' + model[treeiter][1] + ' --> VISIBLE')
            model.set_value (treeiter, 8, 1)
	else:
	    # logging.debug ('recursive_topicstore_match ' + model[treeiter][1] + ' --> INVISIBLE')
            model.set_value (treeiter, 8, 2)

	return visible

    # match a single leaf and set visible flag
    def topicstore_match_leaf(self, model, treeiter):
	if self.match_func (model, treeiter, None):
	    visible = True
	else:
	    visible = False

	if visible:
	    # logging.debug ('topicstore_match_leaf ' + model[treeiter][1] + ' --> VISIBLE')
            model.set_value (treeiter, 8, 1)
	else:
	    # logging.debug ('topicstore_match_leaf ' + model[treeiter][1] + ' --> INVISIBLE')
            model.set_value (treeiter, 8, 2)
	return visible

    # same as recursive_topicstore_match, but for a selected row
    # this is called on insertion of a node (branch or leaf) to set visibility
    @trace
    def topicstore_match_selected(self, model, treeiter):
        # logging.debug ('topicstore_match_selected ' + model[treeiter][1])
        citer = model.iter_children(treeiter)
	if citer is not None:
	    return True

	# could still be a branch, in the process of moving a tree
	# if the name ends with /
	if model[treeiter][1].endswith('/'):
	    # UWE: TEMPORARY
	    # I don't know what to do here, ideally nothing and
	    # let the leaf processing set the visibility of its parents
	    model[treeiter][8] = 2
	    return True

	# a leaf, is it visible?
	visible = self.topicstore_match_leaf (model, treeiter)

	if not visible:
	    self.excluded += 1
	    # logging.debug ('=========== topicstore_match_selected excluded = ' + str(main.excluded))
	else:
	    number = model[treeiter][0]
	    if number not in self.shown_topic_numbers:
	    	# logging.debug ('shown_topic_numbers <--' + str(number) + ' --> len ' + str(len(self.shown_topic_numbers)))
		self.shown_topic_numbers.add(number)

	# now we need to traverse the branches to the root and change their
	# visible flag
	done = False
	piter = treeiter
	while not done:
	    piter = model.iter_parent(piter)
	    if piter is None:
	    	break

	    # logging.debug ('topicstore_match_selected ' + model[piter][1])

	    # change visibility of parent?
	    if visible:
	    	if model[piter][8] == 2:
		    # logging.debug ('topicstore_match_selected ' + model[piter][1] + ' --> 1')
	            model[piter][8] = 1
		continue

	    if model[piter][8] == 2:
	    	continue

	    iter = piter
	    # logging.debug ('***************** check -> ' + model[iter][1])
	    self.recursive_topicstore_match (model, iter, False)

	return visible

    # visibility function for treefilter model
    @trace
    def match_func(self, model, iter, data):
	# logging.debug ("match_func " + model[iter][1])

	# no matching, always visible
	if self.matchprog is None:
	    return True

	# for branches, the visibility flag determines visibility
	if model.iter_children(iter) is not None:
	    # logging.debug ("match_func " + model[iter][1] + ' ' + str(model[iter][8]))
	    return model[iter][8] == 1

	# for leaves, 
	if self.matchprog.search (model[iter][1]) is not None:
	    return True

	return False

    ###############################
    # GUI component, could be separated out into a GUI class
    # the callback on every poller cycle

    # this zeroes out the leaf and/or branch nodes for the numbered topics
    @trace
    def zero_leaf_and_branch_nodes(self, numbersdict, do_leaf):
	numbers = numbersdict.keys()
	for number in numbers:
	    key = numbersdict.get(number, None)
	    if key == None:
	    	logging.error ('number ' + str(number) + ' not in numbersdict')
		continue
            topic =  main.topics.get (key, None)

	    if topic == None:
	    	continue

            if topic.rowref == None:
	    	continue

	    if topic.is_filtered:
		store = main.filteredstore
	    else:
		store = main.topicstore

	    if do_leaf:
	    	store.set_value (topic.rowref, 3, 0)

            # update stats in branches
            topicstr = topic.topic
            # traverse the tree
            tuples = topicstr.split ('/')

            itemi = 0
            for item in tuples:
                itemi += 1
                if itemi >= len(tuples):
                    break

                # top vs. intermediate branch
                if itemi == 1:
                    # does it already exist?
                    piter = store.get_iter_first()
                    while piter is not None:
                        subtopic = store.get_value(piter, 7)
                        if subtopic.decode('utf-8', 'replace') == item.decode('utf-8', 'replace'):
                            store.set_value (piter, 3, 0)
                            break
                        piter = store.iter_next(piter)
                    if piter is None:
                        logging.error (item + " should exist in topicstore - ignored")
                        logging.error ("topic is " + topicstr)
                        return


                else:
                    # does it already exist?
                    citer = store.iter_children(piter)
                    while citer is not None:
                        subtopic = store.get_value(citer, 7)
                        if subtopic.decode('utf-8', 'replace') == item.decode('utf-8', 'replace'):
                            msgpersec = store.get_value(citer, 3)
                            if msgpersec != 0:
                                store.set_value (citer, 3, 0)
                            break
                        citer = store.iter_next(citer)
                    if citer is None:
                        logging.error (item + " should exist in topicstore - ignored")
                        logging.error ("topic is " + topicstr)
                        return

                    piter = citer

    ###############################
    @trace
    def add_new_entry(self, topic):
        topicstr = topic.topic
        # UWE: still get this error
        # Pango-WARNING **: Invalid UTF-8 string passed to pango_layout_set_text()
        # but we don't want the hex string for topic as we
        # do for payload
        # happens much less frequently for topic

        msgpersec = int (topic.count / (self.now - self.last_time))

        # traverse the tree
        tuples = topicstr.split ('/')

	# need to decide which treeview to add to
	# first check if it or any branch path is filtered
        itemi = 0
	path = ''
        for item in tuples:
            itemi += 1

	    path += item
            if itemi < len(tuples):
	        path += '/'
	    # logging.debug ('checking path ' + path)
	    if path in main.filtered_topics:
	    	# logging.debug ('*********** path ' + path + ' is filtered')
	    	topic.is_custom = True
	    	topic.is_filtered = True
		break

	    if path in main.archived_topics:
	    	# logging.debug ('*********** path ' + path + ' is archived')
	    	topic.is_archived = True
		break

	# If not customized, then check message count,
	# if less equal 1, then filtered
	if not topic.is_custom:
	    if topic.count <= 1:
	    	topic.is_filtered = True
		store = main.filteredstore
	    else:
	    	topic.is_filtered = False
		store = main.topicstore
	else:
	    if topic.is_filtered:
		store = main.filteredstore
	    else:
	        store = main.topicstore

	if store == main.topicstore:
	    main.interesting_count += 1
	else:
	    main.filtered_count += 1

	# handle branches only
        itemi = 0
        piter = None
        for item in tuples:
            itemi += 1

            if itemi >= len(tuples):
                break

            # top vs. intermediate branch
            if itemi == 1:
                # does it already exist?
                piter = store.get_iter_first()
                while piter is not None:
                    subtopic = store.get_value(piter, 7)
		    # logging.debug ("*** subtopic %s item %s" % (subtopic, item))
		    if subtopic.decode('utf-8', 'replace') == item.decode('utf-8', 'replace'):
                        break

                    piter = store.iter_next(piter)

                if piter is None:
                    try:
                        utfitem = item.encode('utf-8')
                    except UnicodeError:
                        utfitem = item
		    # logging.debug ("*** append item %s" % (item))
		    if store == main.topicstore:
                        piter = store.append (
                            None,
                            [
                            topic.number,
                            utfitem+"/",
		            0,
		            0,
		            0,
                            "",
                            "",
			    item,
			    0		# visibility undetermined
                            ])
		    else:
                        piter = store.append (
                            None,
                            [
                            topic.number,
                            utfitem+"/",
		            0,
		            0,
		            0,
                            "",
                            "",
			    item
                            ])

            else:
                # does it already exist?
                citer = store.iter_children(piter)
                while citer is not None:
                    subtopic = store.get_value(citer, 7)
		    # logging.debug ("*** subtopic %s item %s" % (subtopic, item))
		    if subtopic.decode('utf-8', 'replace') == item.decode('utf-8', 'replace'):
                        break
                    citer = store.iter_next(citer)

                if citer is None:
                    try:
                        utfitem = item.encode('utf-8')
                    except UnicodeError:
                        utfitem = item
		    # logging.debug ("*** append item %s" % (item))
		    if store == main.topicstore:
                        citer = store.append (
                        piter,
                        [
                        topic.number,
                        utfitem+"/",
		        0,
		        0,
		        0,
                        "",
                        "",
		        item,
		        0		# visibility undetermined
                        ])
		    else:
                        citer = store.append (
                        piter,
                        [
                        topic.number,
                        utfitem+"/",
		        0,
		        0,
		        0,
                        "",
                        "",
		        item
                        ])
                piter = citer

            # update stats for branch node
            messages = topic.count
            bytes = topic.bytes
            # logging.debug ("entry " + store.get_value (piter, 1) + " --> " + str(piter))
	    self._branch_update(store, piter, messages, msgpersec, bytes)

        # recompute timestamp only if different
        # most of the time they are the same
        if int(topic.last_time) != int(self.cached_last_time):
            self.cached_last_time = topic.last_time
            self.timestamp = time.strftime ("%Y/%m/%d-%H:%M:%S", time.localtime (topic.last_time))

        # leaf has complete topic name
	try:
            utftopicstr = topicstr.encode('utf-8')
	except UnicodeError:
            utftopicstr = topicstr
        # UTF-8 encodings
        try:
            payloadstr = topic.last_payload.encode('utf-8')
            #    logging.debug ("payload is UTF-8 " + payloadstr)
        except UnicodeError:
            payloadstr = "0x" + binascii.hexlify (topic.last_payload)
            # logging.debug ("UnicodeError: payload is not UTF-8 " + topic.last_payload + " >> " + payloadstr)


	# logging.debug ("*** append item %s" % (item))
	if store == main.topicstore:
            rowref = store.append(
                piter,
                [
                topic.number,
                utftopicstr,
                topic.count,
                msgpersec,
                topic.bytes,
                self.timestamp,
                payloadstr,
	        '/',		# illegal name
	        0		# visibility undetermined
                ])
	else:
            rowref = store.append(
                piter,
                [
                topic.number,
                utftopicstr,
                topic.count,
                msgpersec,
                topic.bytes,
                self.timestamp,
                payloadstr,
	        '/'		# illegal name
                ])

        topic.rowref = rowref

    ###############################
    @trace
    def update_entry(self, topic, displayedcount, msgpersec):
        if topic.is_filtered:
	    store = main.filteredstore
	else:
	    store = main.topicstore

        displayedbytes = store.get_value (topic.rowref, 4)
        try:
            payloadstr = topic.last_payload.encode('utf-8')
            #    logging.debug ("payload is UTF-8 " + payloadstr)
        except UnicodeError:
            payloadstr = "0x" + binascii.hexlify (topic.last_payload)
            # logging.debug ("UnicodeError: payload is not UTF-8 " + topic.last_payload + " >> " + payloadstr)

        # update stats in branches
        try:
            topicstr = topic.topic.encode('utf-8')
        #    logging.debug ("topic is UTF-8 " + topicstr)
        except UnicodeError:
            topicstr = topic.topic
        # traverse the tree
        tuples = topicstr.split ('/')

        itemi = 0
        for item in tuples:
            itemi += 1
            if itemi >= len(tuples):
                continue

            # top vs. intermediate branch
            if itemi == 1:
                # does it already exist?
                piter = store.get_iter_first()
                while piter is not None:
                    subtopic = store.get_value(piter, 7)
		    if subtopic.decode('utf-8', 'replace') == item.decode('utf-8', 'replace'):
                        break
                    piter = store.iter_next(piter)

                if piter is None:
                    logging.error (item + " should exist in topicstore - ignored")
                    logging.error ("topic is " + topicstr)
                    return

            else:
                # does it already exist?
                citer = store.iter_children(piter)
                while citer is not None:
                    subtopic = store.get_value(citer, 7)
		    if subtopic.decode('utf-8', 'replace') == item.decode('utf-8', 'replace'):
                        break
                    citer = store.iter_next(citer)

                if citer is None:
                    logging.error (item + " should exist in topicstore - ignored")
                    logging.error ("topic is " + topicstr)
                    logging.error ("type(topicstr) " + str(type(topicstr)))
                    logging.error ("type(item) " + str(type(item)))
                    logging.debug ("item " + item)
                    logging.debug ("piter = " + store.get_value(piter, 7))
                    citer = store.iter_children(piter)
                    while citer is not None:
                        subtopic = store.get_value(citer, 7)
                        logging.debug ("subtopic " + subtopic)
		        if subtopic.decode('utf-8', 'replace') == item.decode('utf-8', 'replace'):
                            break
                        citer = store.iter_next(citer)
                    main.dump()
                    sys.exit(1)
                    return

                piter = citer

            # update stats for branch node
            messages = topic.count - displayedcount
            bytes = topic.bytes - displayedbytes

	    #logging.debug ('update ' + store.get_value(topic.rowref, 1) + ' with ' + str(messages) + ' ' + str(msgpersec) + ' ' + str(bytes) )
	    self._branch_update(store, piter, messages, msgpersec, bytes)

        # update the leaf
        # recompute timestamp only if different
        # most of the time they are the same
        if int(topic.last_time) != int(self.cached_last_time):
            self.cached_last_time = topic.last_time
            self.timestamp = time.strftime ("%Y/%m/%d-%H:%M:%S", time.localtime (topic.last_time))
        store.set (topic.rowref, 2, topic.count, 3, msgpersec, 4, topic.bytes, 5, self.timestamp, 6, payloadstr)

    ###############################
    # returns the path from the root to the node given by rowref
    @trace
    def path_of_node(self, store, rowref):
        # if leaf, then the path to the leaf is stored
	if store.iter_children(rowref) is None:
	    return store.get_value (rowref, 1)

        # for a branch, we need to build it
	iterlist = list()
	while rowref is not None:
	    iterlist.append (rowref)
	    rowref = store.iter_parent(rowref)
    	retpath = ''
	while len(iterlist) > 0:
	    rowref = iterlist.pop()
	    retpath = retpath + store.get_value (rowref, 7) + '/'
	return retpath


    ###############################
    @trace
    def _branch_update(self, store, rowref, messages, msgpersec, bytes):
        messages += store.get_value (rowref, 2)
        msgpersec += store.get_value (rowref, 3)
        bytes += store.get_value (rowref, 4)
        store.set (rowref, 2, messages, 3, msgpersec, 4, bytes)

    ###############################
    @trace
    def completed_cb(self, thread):
        # logging.debug ("completed_cb - enter")

	# update is paused
	if self.do_pause:
	    return

	self.last_time = self.now
	self.now = time.time()

        # according to https://en.wikibooks.org/wiki/GTK%2B_By_Example/Tree_View/Tree_Models#Speed_Issues_when_Adding_a_Lot_of_Rows
        # while we update the model, detach it from the view
        # but this doubles CPU usage, and no apparent benefits
        # model = self.treeview.get_model()
        # self.treeview.set_model (None)

        if self.clear_store:
            self.topicstore.clear()
            self.filteredstore.clear()
	    self.interesting_count = 0
	    self.filtered_count = 0
	    self.excluded = 0
	    self.shown_topic_numbers.clear()
            self.clear_store = False

        msgpersec = int((self.messages_received - self.last_received) / (self.now - self.last_time))
        self.last_received = self.messages_received
        main.infolabel3.set_markup('<span foreground="blue">Messages received: ' + str(main.messages_received) + '</span>')
        main.infolabel5.set_markup('<span foreground="blue">Messages / sec: ' + str(msgpersec) + '</span>')
        main.infolabel4.set_markup('<span foreground="green">Topics: ' + str(len(main.topics)) + '</span>')

        # run through the topics and add to the matrix

	# take a snapshot of main.changed and main.last_changed
	changed = main.changed
	last_changed = main.last_changed
	main.last_changed = main.changed
	main.changed = dict()

	logging.debug ('changed ' + str (len(changed)) + ' last_changed ' + str (len (last_changed)))

        # 2 loops, the first to clear msgpersec in existing
        # branch nodes for changed leaf nodes
	# last_changed has the nodes that were updated last time, they
	# need to be zeroed out now
        self.zero_leaf_and_branch_nodes (last_changed, True)
	last_changed.clear()
        self.zero_leaf_and_branch_nodes (changed, False)

        # the second to actually update the stats
        # new entries are added immediately to the tree model
        # updates are collected into a dictionary (de-duplicated), then 
        # we handle them later
        active_topics = 0
        updated_entries = 0
        new_entries = 0
	moved_entries = 0
        self.cached_last_time = 0
        self.timestamp = ''
	# if this is non-zero, will throttle updates to the selected max
        # max_entries = 400
        max_entries = 0
	numbers = changed.keys()
	for number in numbers:
	    key = changed.get(number, None)
	    if key == None:
	    	logging.error ('number ' + str(number) + ' not in changed')
		continue

	    # different throttling techniques:
	    # alternative 1 handle at most 400 updates at once
	    # miss out on updates
            #if max_entries > 0 and updated_entries + new_entries > max_entries:
            #	break

            topic =  main.topics.get (key, None)

	    if topic == None:
	    	continue

            if topic.rowref == None:

                self.add_new_entry (topic)

                active_topics += 1
                new_entries += 1

		# alternative 2 handle at most 400 new entries at once
		if max_entries > 0 and new_entries >= max_entries:
		 	break
            else:

		if topic.is_filtered:
		    store = main.filteredstore
		else:
		    store = main.topicstore

                # redisplay only if new messages for topic
                displayedcount = store.get_value (topic.rowref, 2)
		deltamessages = (topic.count - displayedcount)
                msgpersec = int(deltamessages / (self.now - self.last_time))
                displayedmsgpersec = store.get_value (topic.rowref, 3)
                do_display = True
                if topic.count != displayedcount:
                    active_topics += 1

                if do_display:

		    # move from filtered to interesting
		    do_move = False
		    if not topic.is_custom:
		        if topic.is_filtered and topic.count > 1:
			    do_move = True

		    if do_move:
                        moved_entries += 1
			displayedbytes = store.get_value (topic.rowref, 4)
		        # logging.debug ('moving ' + store.get_value(topic.rowref, 1) + ' with ' + str(displayedcount) + ' ' + str(displayedmsgpersec) + ' ' + str(displayedbytes) )
			piter = store.iter_parent(topic.rowref)
			store.remove (topic.rowref)
			topic.rowref = None

			# remove any stale branches, and update stats
			while piter is not None:
			    citer = store.iter_children(piter)
			    parent = store.iter_parent(piter)
			    if citer is not None:
			        # has children, don't remove, but update
			    	# it's stats, by subtracting the topic's
				# previous stats
				# we already zeroed msgpersec in zero_branch*
				# above, no need
				self._branch_update(store, piter, -displayedcount, 0, -displayedbytes)
			    else:
				    store.remove (piter)
			    piter = parent

			main.filtered_count -= 1
			topic.is_filtered = False
			self.add_new_entry (topic)

		    else:
                        updated_entries += 1
			# alternative 3: only update 400 entries
			if max_entries > 0 and updated_entries + new_entries > max_entries:
				continue
                        self.update_entry (topic, displayedcount, msgpersec)


        logging.debug ('new entries ' + str(new_entries) + ' updated ' + str(updated_entries) + ' moved ' + str(moved_entries))

        # restore the model detached above
        # self.treeview.set_model (model)

        main.infolabel6.set_markup('<span foreground="green">Active topics: ' + str(active_topics) + '</span>')
        main.infolabel7.set_markup('<span foreground="blue">Interesting: ' + str(main.interesting_count) + '</span>')
        main.infolabel8.set_markup('<span foreground="blue">Filtered: ' + str(main.filtered_count) + '</span>')
	if main.matchprog is not None:
	    main.infolabel9.set_markup('<span foreground="red">Excluded: ' + str(main.excluded) + ' </span><span foreground="green">Shown: ' + str(len(main.shown_topic_numbers)) + '</span>')
	else:
	    main.infolabel9.set_text("")
	main.infolabel13.set_markup ('<span foreground="blue">Archived: ' + str(main.archived_count) + '</span>')
	main.infolabel14.set_markup ('<span foreground="green">Archiving: ' + str(len(main.archived_topics)) + '</span>')
        # logging.debug ("completed_cb - exit")

    def dump(self):
        outfile = open ("dump.lst", "w+")
        print ("Number Topic                Messages Bytes Last payload", file=outfile)
        keys = self.topics.keys()
        for key in keys:
            topic =  self.topics[key]

            topic.dump(outfile)

        outfile.close()
        print ("dumped to dump.lst")
        return

    def quit(self):
        # client.loop_stop()
        self.is_stopped = True

	# persistency
	# dump the filtered_topics
        outfile = open ("filtered.cfg", "w+")
	pickle.Pickler(outfile).dump(main.filtered_topics)
        outfile.close()
        return

#    def on_treeview_select_func(selection, model, path, selected, data):
#	    return False  # Can't select this row

    def status_message (self, text):
        id1 = self.statusbar.get_context_id("Statusbar")
	self.statusbar.pop (id1)
	self.statusbar.push (id1, text)

    def on_topicstore_selection_changed(self, selection):
	self.filter_button.set_sensitive (True)
	self.copy_button.set_sensitive (True)
	self.expand_button.set_sensitive (True)
    	model, pathlist = selection.get_selected_rows()
	count = 0
	archived_count = 0
	for path in pathlist:
	    count += 1
	    treeiter = model.get_iter(path)
	    topicpath = main.path_of_node(model, treeiter)
	    if topicpath in main.archived_topics:
	        archived_count += 1
	    logging.debug ('********* selection ' + topicpath)

        # set the state of the archive button: if all archived, then it's
	# depressed, else if all not-archived, then released, else greyed
	if archived_count == 0 or archived_count == count:
	    self.archive_button.set_sensitive (True)
	    if archived_count == 0:
	        self.archive_button.set_active(False)
	    else:
	        self.archive_button.set_active(True)
	else:
	    self.archive_button.set_sensitive (False)

	self.status_message ('selected ' + str (count) + ' rows')

###########################################################################
class Handler:
    def on_mainWindow_delete_event(self, *args):
        Gtk.main_quit(*args)
        main.quit()
    
    def on_gtk_quit_activate(self, menuitem, data=None):
        Gtk.main_quit()
        main.quit()

    # File->New menu handler
    def on_gtk_filenew_activate(self, menuitem, data=None):
        # clear everything
        main.infolabel3.set_text("")
        main.infolabel4.set_text("")

        main.do_clear_stats = True

    # File->Save menu handler
    def on_gtk_filesave_activate(self, menuitem, data=None):
        main.dump()

    # Help->About menu handler
    def on_gtk_about_activate(self, menuitem, data=None):
        self.response = main.aboutdialog.run()
        main.aboutdialog.hide()
    
    # Help->Help menu handler
    def on_gtk_content_activate(self, menuitem, data=None):
	webbrowser.open_new_tab("https://www.gambitcommunications.com/update/doc/mqtt-explorer.htm")

    # Pause toggle button handler, globally pauses/restores the update
    def on_pause_button_toggled(self, button, data=None):
        logging.debug ("on_pause_button_toggled " + str(button))
	if not main.do_pause:
	    main.do_pause = True
	    paused = ' (paused)'
	else:
	    main.do_pause = False
	    paused = ' (resumed)'
        main.infolabel3.set_markup('<span foreground="blue">Messages received: ' + str(main.messages_received) + '</span>' + paused)


    # the only way of disabling sorting I found after googling is to
    # set the default sort function to always return "equal" for the 2 rows
    # and set sort column id to -1
    def _disable_sort_func (self, model, row1, row2, user_data):
#    	sort_column, _ = model.get_sort_column_id()
#	value1 = model.get_value(row1, sort_column)
#	value2 = model.get_value(row2, sort_column)
#	print ("sort id " + str(sort_column) + " v1 " + value1 + ", v2 " + value2)
    	return 0

    # Freeze toggle button handler, globally pauses/restores the sorting
    def on_freeze_button_toggled(self, button, data=None):
        logging.debug ("on_freeze_toggled " + str(button))
	print ("get_sort_column_id " + str(main.treeview.get_model().get_sort_column_id()))

	if not main.do_freeze:
    	    main.sort_column, main.sort_order = main.treeview.get_model().get_sort_column_id()
	    main.treeview.get_model().set_default_sort_func (self._disable_sort_func)
    	    main.treeview.get_model().set_sort_column_id(-1, Gtk.SortType.ASCENDING)
	    main.do_freeze = True
	else:
	    if main.sort_column is not None:
		    main.treeview.get_model().set_sort_column_id(main.sort_column, main.sort_order)
	    main.do_freeze = False

	return

    # recursively move the tree under treeiter in tropicstore to the
    # filtered store
    # returns number of leaf topics moved
    def move_to_filtered (self, treeiter):
    	store = main.topicstore
	count = 0

        # logging.debug ("move_to_filtered " + store[treeiter][1])
	topicstr = store.get_value (treeiter, 1)
        citer = store.iter_children(treeiter)

	# no more children, move the topic
	if citer is None:
		topic =  main.topics.get (topicstr, None)
		if topic is None:
                    logging.error (topicstr + " should exist in main.topics - ignored")
		    return

		# logging.debug ('remove ' + store[treeiter][1])
		store.remove (treeiter)
		topic.rowref = None
	        main.interesting_count -= 1
		topic.is_filtered = True
		topic.is_custom = True

		main.add_new_entry (topic)
		
		return 1

	# move all the children
	while citer is not None:
		nextiter = store.iter_next(citer)
		count += self.move_to_filtered (citer)
		citer = nextiter

	# then remove it
	# logging.debug ('remove ' + store[treeiter][1])
	store.remove (treeiter)

	return count

    # Filter button handler - move all selected sub-tree(s) to filtered store
    def on_gtk_filter_clicked(self, button, data=None):
        logging.debug ("on_gtk_filter_clicked " + str(button))

	selection = main.treeview.get_selection()
    	model, pathlist = selection.get_selected_rows()

	# need to collect the iterators for the paths, because
	# modifying the tree invalidates the paths
	iterlist = list()
	for path in pathlist:
	    treeiter = model.get_iter(path)
	    if not model.iter_is_valid(treeiter):
	    	logging.debug ('treeiter is not valid')
	        continue
	    filter_iter = model.convert_iter_to_child_iter(treeiter)
	    store_iter = model.get_model().convert_iter_to_child_iter(filter_iter)
	    iterlist.append(store_iter)

	# move all the leaf topics under the node to the filtered treestore
	count = 0
	for store_iter in iterlist:
	    topicpath = main.path_of_node(main.topicstore, store_iter)
	    logging.debug ('********** filtering ' + topicpath)
	    if topicpath not in main.filtered_topics:
	    	main.filtered_topics.add(topicpath)

	    parent = main.topicstore.iter_parent(store_iter)
	    count += self.move_to_filtered (store_iter)

	    main.status_message ('filtered ' + str (count) + ' topics')

	    # need to check its parents to see if they can be removed as well
	    treeiter = parent
	    while treeiter is not None:
	        citer = main.topicstore.iter_children(treeiter)
	        if citer is not None:
	        	break
	        parent = main.topicstore.iter_parent(treeiter)
	        main.topicstore.remove (treeiter)
		treeiter = parent

    # Copy button handler - copy selected node(s) to clipboard
    def on_gtk_copybutton_clicked(self, button, data=None):
        # logging.debug ("on_gtk_copybutton_clicked " + str(button))

	clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)

	selection = main.treeview.get_selection()
    	model, pathlist = selection.get_selected_rows()
	text=''
	count = 0
	for path in pathlist:
	    treeiter = model.get_iter(path)
	    # only leaves
	    rettext, retcount = self._recursive_text_leaf_nodes(model, treeiter)
	    text += rettext
	    count += retcount

	clipboard.set_text(text, -1)   

	main.status_message ('copied ' + str (count) + ' topics to clipboard')

    # recursively return the string representation of all leaf nodes under
    # treeiter
    # returns text, count of nodes
    def _recursive_text_leaf_nodes(self, model, treeiter):
        citer = model.iter_children(treeiter)
	# no more children, copy the leaf
	if citer is None:
	    text = str (model.get_value (treeiter, 0)) + '\t'
	    text += str(model.get_value (treeiter, 1)) + '\t'
	    text += str(model.get_value (treeiter, 2)) + '\t'
	    text += str(model.get_value (treeiter, 3)) + '\t'
	    text += str(model.get_value (treeiter, 4)) + '\t'
	    text += str(model.get_value (treeiter, 5)) + '\t'
	    text += str(model.get_value (treeiter, 6)) + '\n'
	    return text, 1

	text = ''
	count = 0
	while citer is not None:
		rettext, retcount = self._recursive_text_leaf_nodes (model, citer)
		text += rettext
		count += retcount
		citer = model.iter_next(citer)
	return text, count

    # Expand button handler
    def on_gtk_expandbutton_clicked(self, button, data=None):
        # logging.debug ("on_gtk_expand_clicked " + str(button))

	selection = main.treeview.get_selection()
    	model, pathlist = selection.get_selected_rows()
	for path in pathlist:
	    main.treeview.expand_row (path, True)

    def on_archive_button_toggled(self, button, data=None):
        logging.debug ("on_archive_button_toggled " + str(button))

	selection = main.treeview.get_selection()
    	model, pathlist = selection.get_selected_rows()
	text=''
	count = 0
	for path in pathlist:
	    treeiter = model.get_iter(path)
	    topicpath = main.path_of_node(model, treeiter)
	    logging.debug ('********** archiving ' + topicpath)

	    if topicpath in main.archived_topics:
	        main.archived_topics.remove (topicpath)
	        do_archive = 0
	    else:
	        main.archived_topics.add (topicpath)
	        do_archive = 1

	    count += self._recursive_archive_leaf_nodes (model, treeiter, do_archive)

	if do_archive == 1:
	    main.status_message ('started archiving ' + str (count) + ' topics')
	else:
	    main.status_message ('stopped archiving ' + str (count) + ' topics')

    # recursively set archive flag and return the count of all leaf nodes
    # under treeiter
    def _recursive_archive_leaf_nodes(self, model, treeiter, do_archive):
        citer = model.iter_children(treeiter)
	# no more children, copy the leaf
	if citer is None:
	    topicstr = model.get_value (treeiter, 1)
	    topic =  main.topics.get (topicstr, None)
	    if topic is None:
                logging.error (topicstr + " should exist in main.topics - ignored")
		return 0

	    if do_archive == 1:
	        topic.is_archived = True
		topic.archive()
		main.archived_count += 1
	    else:
	        topic.is_archived = False
	    return 1

	count = 0
	while citer is not None:
		retcount = self._recursive_archive_leaf_nodes (model, citer, do_archive)
		count += retcount
		citer = model.iter_next(citer)
	return count

    def on_treeview1_button_press_event (self, model, event):
        # logging.debug ("********* on_treeview1_button_press_event " + str(event))
	if event.button == 3:
	    logging.debug ('********* right click')
	    main.topicstoremenu.show_all()

	# propagate the event
	return False
	

    # Text entry handler - the entered text becomes a regex to match entries
    # in the treeview to immediately focus on certain topics
    def on_gtk_entry_changed(self, item, data=None):
	# logging.debug ("on_gtk_entry_changed " + item.get_text())
	if item.get_text() == '':
	    main.matchprog = None
	else:
	    try:
		main.matchprog = re.compile(item.get_text())
	    except:
		main.matchprog = None

	# we need to run through the entire tree and set visible flag for all
	# nodes
	main.excluded = 0
	main.shown_topic_numbers.clear()
	main.recursive_topicstore_match (main.topicstore, main.topicstore.get_iter_first(), True)

	# then force an update to all topics
	# by setting last_changed, we force all of them to be revisited at
	# the next completed_cb invocation
	for topic in main.topics:
	    entry = main.topics[topic]
	    if entry.number not in main.last_changed:
		main.last_changed[entry.number] = topic
	return

    def on_gtk_topicstore_row_inserted(self, model, path, iter):
	# logging.debug ('*********** on_gtk_topicstore_row_inserted ' + model[iter][1])
	main.topicstore_match_selected (main.topicstore, iter)

###########################################################################
if __name__ == "__main__":
    GObject.threads_init()

    main = MyApp()
    main.start()
