#!/usr/bin/env python3

"""
Automatically track working time in hamster.

Monitor DBUS for session lock/unlock events and start a hamster task
on the 1st unlock on a day end end the running hamster task when
screen is locked. On subsequent unlock/lock events, extend the end
time of the latest task for the current day.

"""

# activity to use when creating facts
default_activity = 'Work'
default_category = 'Work'
loglevel = 'DEBUG'

import dbus
import dbus.service

import gi
from gi.repository import GLib as glib
gi.require_version('GConf', '2.0')
from gi.repository import GConf as gconf

from hamster.lib import datetime as dt
from hamster.lib import default_logger
from hamster.lib.fact import Fact
from hamster.lib.dbus import (
    DBusMainLoop,
    fact_signature,
    from_dbus_date,
    from_dbus_fact,
    from_dbus_fact_json,
    from_dbus_range,
    to_dbus_date,
    to_dbus_fact,
    to_dbus_fact_json
)
from hamster.lib.fact import Fact, FactError
logger = default_logger(__file__)

DBusMainLoop(set_as_default=True)
loop = glib.MainLoop()

class AutoTracker():
    def __init__(self, logger = logger):
        self.logger = logger
        logger.setLevel(loglevel)

        self.mainloop = loop
        self.bus = dbus.SessionBus()
        s = self.bus.get_object('org.gnome.ScreenSaver', '/')
        self.screensaver = dbus.Interface(s, dbus_interface='org.gnome.ScreenSaver')

        self.screensaver_active = self.is_screensaver_active()
        self.screen_locked = False

        # read screensaver/lock delay from gconf (only on startup, if
        # a user changes this later, we will be wrong)
        # There are multiple ways this might configured, depending on
        # the gnome version. These older places aren't checked, but
        # maybe somebody needs to:
        # '/apps/gnome-screensaver/idle_delay',
        # '/desktop/gnome/session/idle_delay',
        delay_key = '/org/gnome/desktop/session/idle-delay'
        client = gconf.Client.get_default()
        d = client.get_int(delay_key)
        if d:
            self.timeout_minutes = d/60
        else:
            self.timeout_minutes = 0

        h = self.bus.get_object('org.gnome.Hamster', '/org/gnome/Hamster')
        self.hamster = dbus.Interface(h, dbus_interface='org.gnome.Hamster')


    def run(self):
        self.logger.info("Starting up.")

        # get notified of screen locked events (either due to idle
        # timeout or due to users locking their screen)
        self.bus.add_signal_receiver(dbus_interface='org.gnome.ScreenSaver',
                                     signal_name='ActiveChanged',
                                     handler_function=self.on_active_changed)
        # monitor calls to org.gnome.ScreenSaver.Lock
        self.bus.add_match_string("interface='org.gnome.ScreenSaver',member='Lock',eavesdrop='true'")
        self.bus.add_message_filter(self.on_locked)

        self.mainloop.run()
        self.logger.info("Mainloop ended.")


    def is_screensaver_active(self):
        act = self.screensaver.GetActive()
        self.logger.debug(f'ScreenSaver active: {act}')
        return act


    def on_locked(self, bus, msg):
        if msg.get_interface() == 'org.gnome.ScreenSaver' and msg.get_member() == 'Lock':
            self.logger.debug('Lock received')
            self.screen_locked = True


    def on_active_changed(self, screensaver_active):
        self.logger.debug(f"active changed called, args: {screensaver_active}")
        self.screensaver_active = screensaver_active
        if self.screensaver_active:
            self.logger.info('stop current activity')
            self.stop_activity()
        else:
            self.logger.info('resume or start new activity')
            self.screen_locked = False
            self.resume_activity()


    def idle_start(self):
        """Return the time when idleness started.

If screen was explicitly locked, this is now. If screen was
deactivated due to idleness, subtract screensaver delay from current
time.
        """
        if not(self.screen_locked) and self.timeout_minutes:
            return dt.datetime.now() - dt.timedelta(minutes=self.timeout_minutes)
        else:
            return dt.datetime.now()


    def stop_activity(self):
        ret = self.hamster.StopTracking(to_dbus_date(self.idle_start()))
        self.logger.info(f'Stopped tracking, ret: {ret}')
        return ret


    def get_todays_activities(self):
        return self.hamster.GetTodaysFactsJSON()


    def resume_activity(self):
        activities = self.get_todays_activities()
        if activities:
            act = from_dbus_fact_json( activities[-1])
            act.range.end = None
            json = to_dbus_fact_json(act)
            self.logger.debug(f'Update fact: {json}' )
            ret = self.hamster.UpdateFactJSON(act.id, json)
            self.logger.debug(f'Updated fact, ret: {ret}')
        else:
            act = Fact(category=default_category,
                       activity=default_activity,
                       start=dt.datetime.now())
            ret = self.hamster.AddFactJSON(to_dbus_fact_json(act))
            self.logger.debug(f'Added fact, ret: {ret}')
        return ret



if __name__ == '__main__':
    t = AutoTracker()
    t.run()
