from cbpi.api import *
from cbpi.api import parameters, Property, action
from cbpi.api.step import StepResult, CBPiStep
from cbpi.api.timer import Timer
from cbpi.api.dataclasses import NotificationAction, NotificationType
from cbpi.api.dataclasses import Kettle, Props
from cbpi.api.config import ConfigType
from cbpi.api.base import CBPiBase

import asyncio
import logging
import time
import asyncio
from datetime import datetime

from gpiozero import CPUTemperature

# from voluptuous.schema_builder import message
# from socket import timeout
# from typing import KeysView
# import numpy as np
# import warnings

################################################################################
@parameters([Property.Number(label="Temp", configurable=True),
             Property.Sensor(label="Sensor"),
             Property.Kettle(label="Kettle"),
             Property.Select(label="AutoMode",options=["Yes","No"], description="Switch Kettlelogic automatically on and off -> Yes"),
             Property.Select(label="Agitate",options=["Yes","No"])])
class KettleStep(CBPiStep):
    async def on_timer_done(self, timer):
        self.summary = ""
        await self.next()

    async def on_timer_update(self, timer, seconds):
        self.summary = Timer.format_time(seconds)
        await self.push_update()

    async def on_start(self):
        self.kettle=self.get_kettle(self.props.get("Kettle", None))
        if self.kettle is not None:
            self.kettle.target_temp = int(self.props.get("Temp", 0))

        self.AutoMode = True if self.props.get("AutoMode","No") == "Yes" else False
        await self.setAutoMode(self.AutoMode)

        if self.props.get("Agitate","No") == "Yes":
            await self.actor_on(self.kettle.agitator)
        else:
            await self.actor_off(self.kettle.agitator)

        self.summary = "Waiting for Target Temp"
        if self.cbpi.kettle is not None and self.timer is None:
            self.timer = Timer(1 ,on_update=self.on_timer_update, on_done=self.on_timer_done)
        await self.push_update()

    async def on_stop(self):
        await self.timer.stop()
        self.summary = ""
        await self.push_update()

    async def reset(self):
        self.timer = Timer(1, on_update=self.on_timer_update, on_done=self.on_timer_done)

    async def run(self):
        while self.running == True:
            await asyncio.sleep(1)
        return StepResult.DONE

    async def setAutoMode(self, auto_state):
        try:
            if (self.kettle.instance is None or self.kettle.instance.state == False) and (auto_state is True):
                await self.cbpi.kettle.toggle(self.kettle.id)
            elif (self.kettle.instance.state == True) and (auto_state is False):
                await self.cbpi.kettle.stop(self.kettle.id)
            await self.push_update()

        except Exception as e:
            logging.error("Failed to switch on KettleLogic {} {}".format(self.kettle.id, e))


################################################################################
@parameters([Property.Number(label="P", configurable=True, default_value=117.0795,
                             description="P Value of PID"),
             Property.Number(label="I", configurable=True, default_value=0.2747,
                             description="I Value of PID"),
             Property.Number(label="D", configurable=True, default_value=41.58,
                             description="D Value of PID"),
             Property.Select(label="SampleTime", options=[2,5],
                             description="PID Sample time in seconds. Default: 5 (How often is the output calculation done)"),
             Property.Number(label="PID_Power_Level", configurable=True, default_value=50,
                             description="Power level in PID mode."),
             Property.Number(label="Max_Power_Delta", configurable=True, default_value=5,
                             description="Full power if temp is this far below target."),
             Property.Number(label="Max_Power_Level", configurable=True, default_value=100,
                             description="Max power for fastest heating."),
             Property.Number(label="Boil_Target_Threshold", configurable=True, default_value=90,
                             description="Boil mode if target is above this temp."),
             Property.Number(label="Boil_Power_Level", configurable=True, default_value=100,
                             description="Power level in PID mode."),
             Property.Number(label="Max_Pump_Temp", configurable=True, default_value=88,
                             description="Max temp the pump can work in."),
             Property.Select(label="Pump_Boil_State", options=["On","Off"],
                             description="Pump state in boil mode.")])
class PersonalPIDBoil(CBPiKettleLogic):

    def __init__(self, cbpi, id, props):
        super().__init__(cbpi, id, props)
        self._logger = logging.getLogger(type(self).__name__)
        self.sample_time, self.pid_output, self.pid = None, None, None
        self.max_output, self.boil_output, self.pump_boil_state = None, None, None
        self.boil_temp, self.temp_delta, self.max_pump_temp = None, None, None
        self.kettle, self.heater, self.agitator = None, None, None

    async def on_stop(self):
        # ensure to switch also pump off when logic stops
        await self.actor_off(self.agitator)

    # subroutine that controls pump aue and ump stop if max pump temp is reached
    async def pump_control(self):
        #get pump based on agitator id
        self.pump = self.cbpi.actor.find_by_id(self.agitator)

        while self.running:
            # get current pump status
            pump_on = self.pump.instance.state
            # get current temeprature
            current_temp = self.get_sensor_value(self.kettle.sensor).get("value")
            # get the current target temperature for the kettle
            target_temp = self.get_kettle_target_temp(self.id)

            if  (current_temp > self.max_pump_temp) or ;
                ((target_temp > self.boil_temp) and self.pump_boil_state):
                if pump_on:
                    self._logger.debug("turning pump off")
                    await self.actor_off(self.agitator)

            else:
                if not pump_on:
                    self._logger.debug("starting pump")
                    #switch the pump on
                    await self.actor_on(self.agitator)

            await asyncio.sleep(1)

    # subroutine that controlls temperature via pid controll
    async def temp_control(self):
        await self.actor_on(self.heater,0)
        logging.info("Heater on with zero Power")
        heat_percent_old = 0
        while self.running:
            # get current heater power
            current_kettle_power= self.heater_actor.power
            # get current temeprature
            current_temp = self.get_sensor_value(self.kettle.sensor).get("value")
            # get the current target temperature for the kettle
            target_temp = self.get_kettle_target_temp(self.id)


            # if current temperature is more than defined value below target, max output will be used until temp is closer to taget
            if current_temp <= target_temp - self.temp_delta:
                heat_percent = self.max_output

            # if target temperature is higher the defined boil temp, use fixed heating percent instead of PID values for controlled boiling
            elif target_temp >= self.boil_temp:
                heat_percent = self.boil_output

            # at other temepratures, PID algorythm will valculate heat percent value
            else:
                heat_percent = self.pid.calc(current_temp, target_temp)

            # Test with actor power
            if (heat_percent != heat_percent_old) or (heat_percent != current_kettle_power):
                await self.actor_set_power(self.heater,heat_percent)
                heat_percent_old = heat_percent

            await asyncio.sleep(self.sample_time)

    async def run(self):
        try:
            self.pid_output = int(self.props.get("PID_Power_Level",50))
            p = float(self.props.get("P", 117.0795))
            i = float(self.props.get("I", 0.2747))
            d = float(self.props.get("D", 41.58))
            self.sample_time = int(self.props.get("SampleTime",5))
            self.pid = PIDArduino(self.sample_time, p, i, d, 0, self.pid_output)

            self.max_output = int(self.props.get("Max_Power_Level",100))
            self.boil_output = int(self.props.get("Boil_Power_Level",100))

            self.TEMP_UNIT = self.get_config_value("TEMP_UNIT", "C")
            boilthreshold = 98 if self.TEMP_UNIT == "C" else 208
            maxpumptemp = 88 if self.TEMP_UNIT == "C" else 190
            tempdelta = 5 if self.TEMP_UNIT == "C" else 8

            self.boil_temp = float(self.props.get("Boil_Target_Threshold", boilthreshold))
            self.temp_delta = float(self.props.get("Max_Power_Threshold", tempdelta))
            self.max_pump_temp = float(self.props.get("Max_Pump_Temp", maxpumptemp))

            self.pump_boil_state = True if self.props.get("Pump_Boil_State","Off") == "On" else False

            self.kettle = self.get_kettle(self.id)
            self.heater = self.kettle.heater
            self.agitator = self.kettle.agitator
            self.heater_actor = self.cbpi.actor.find_by_id(self.heater)

            logging.info("CustomLogic P:{} I:{} D:{} {} {}".format(p, i, d, self.kettle, self.heater))

            pump_controller = asyncio.create_task(self.pump_control())
            temp_controller = asyncio.create_task(self.temp_control())

            await pump_controller
            await temp_controller

        except asyncio.CancelledError as e:
            pass
        except Exception as e:
            logging.error("PersonalPIDBoil Error {}".format(e))
        finally:
            self.running = False
            await self.actor_off(self.heater)


# Based on Arduino PID Library
# See https://github.com/br3ttb/Arduino-PID-Library
class PIDArduino(object):

    def __init__(self, sampleTimeSec, kp, ki, kd, outputMin=float('-inf'),
                 outputMax=float('inf'), getTimeMs=None):
        if kp is None:
            raise ValueError('kp must be specified')
        if ki is None:
            raise ValueError('ki must be specified')
        if kd is None:
            raise ValueError('kd must be specified')
        if float(sampleTimeSec) <= float(0):
            raise ValueError('sampleTimeSec must be greater than 0')
        if outputMin >= outputMax:
            raise ValueError('outputMin must be less than outputMax')

        self._logger = logging.getLogger(type(self).__name__)
        self._Kp = kp
        self._Ki = ki * sampleTimeSec
        self._Kd = kd / sampleTimeSec
        self._sampleTime = sampleTimeSec * 1000
        self._outputMin = outputMin
        self._outputMax = outputMax
        self._iTerm = 0
        self._lastInput = 0
        self._lastOutput = 0
        self._lastCalc = 0

        if getTimeMs is None:
            self._getTimeMs = self._currentTimeMs
        else:
            self._getTimeMs = getTimeMs

    def calc(self, inputValue, setpoint):
        now = self._getTimeMs()

        if (now - self._lastCalc) < self._sampleTime:
            return self._lastOutput

        # Compute all the working error variables
        error = setpoint - inputValue
        dInput = inputValue - self._lastInput

        # In order to prevent windup, only integrate if the process is not saturated
        if self._outputMax > self._lastOutput > self._outputMin:
            self._iTerm += self._Ki * error
            self._iTerm = min(self._iTerm, self._outputMax)
            self._iTerm = max(self._iTerm, self._outputMin)

        p = self._Kp * error
        i = self._iTerm
        d = -(self._Kd * dInput)

        # Compute PID Output
        self._lastOutput = p + i + d
        self._lastOutput = min(self._lastOutput, self._outputMax)
        self._lastOutput = max(self._lastOutput, self._outputMin)

        # Log some debug info
        self._logger.debug('P: {0}'.format(p))
        self._logger.debug('I: {0}'.format(i))
        self._logger.debug('D: {0}'.format(d))
        self._logger.debug('output: {0}'.format(self._lastOutput))

        # Remember some variables for next time
        self._lastInput = inputValue
        self._lastCalc = now
        return self._lastOutput

    def _currentTimeMs(self):
        return time.time() * 1000


################################################################################
@parameters([Property.Sensor(label="Sensor1"),
             Property.Sensor(label="Sensor2"),
             Property.Sensor(label="Sensor3"),
             Property.Sensor(label="Sensor4"),
             Property.Sensor(label="Sensor5"),
             Property.Sensor(label="Sensor6"),
             Property.Sensor(label="Sensor7"),
             Property.Sensor(label="Sensor8"),
             Property.Select(label="Value_Type", option=["Avg","Min","Max"]),
             Property.Number(label="Ignore_Threshold", configurable=True, default_value=5),
             Property.Number(label="Sample_Time", configurable=True, default_value=5)])
class GroupSensor(CBPiSensor):

    def __init__(self, cbpi, id, props):
        super().__init__(cbpi, id, props)
        self.value = 0
		self.sensors = []
		if self.props.get("Sensor1",None):
			self.sensors.append(self.props["Sensor1"])
		if self.props.get("Sensor2",None):
			self.sensors.append(self.props["Sensor2"])
		if self.props.get("Sensor3",None):
			self.sensors.append(self.props["Sensor3"])
		if self.props.get("Sensor4",None):
			self.sensors.append(self.props["Sensor4"])
		if self.props.get("Sensor5",None):
			self.sensors.append(self.props["Sensor5"])
		if self.props.get("Sensor6",None):
			self.sensors.append(self.props["Sensor6"])
		if self.props.get("Sensor7",None):
			self.sensors.append(self.props["Sensor7"])
		if self.props.get("Sensor8",None):
			self.sensors.append(self.props["Sensor8"])
        self.type = self.props.get("Value_Type","Avg")
        self.ignore = int(self.props.get("Ignore_Threshold","0"))
        self.sample = int(self.props.get("Sample_Time","5"))
        if self.sample <= 0:
            self.sample = 5

    async def run(self):
        while self.running:
            values = [self.get_sensor_value(sensor) for sensor in self.sensors]
            max_value = max(values)
            if self.ignore:
                temp_values = []
                for value in values:
                    if max_value - value < self.ignore:
                        temp_values.append(value)
                values = temp_values

            if self.type == "Max":
                self.value = max_value
            elif self.type = "Min":
                self.value = min(values)
            elif self.type == "Avg":
                self.value = sum(values)/len(values)
            else:
                self.value = 0.0

            self.log_data(self.value)
            self.push_update(self.value)
            await asyncio.sleep(self.sample)

    def get_state(self):
        return dict(value=self.value)


################################################################################
parameters([])
class CPUTemp(CBPiSensor):

    def __init__(self, cbpi, id, props):
        super(SystemSensor, self).__init__(cbpi, id, props)
        self.value = 0
        self.TEMP_UNIT=self.get_config_value("TEMP_UNIT", "C")

    async def run(self):
        while self.running is True:
            try:
                temp = CPUTemperature()
                if self.TEMP_UNIT == "C": # Report temp in C if nothing else is selected in settings
                    self.value = round(temp,1)
                else: # Report temp in F if unit selected in settings
                    self.value = round((9.0 / 5.0 * temp + 32), 1)
                self.log_data(self.value)

            except Exception as e:
                logging.info(e)
            pass

            self.push_update(self.value)
            await asyncio.sleep(5)

    def get_state(self):
        return dict(value=self.value)

################################################################################
def setup(cbpi):
    '''
    This method is called by the server during startup
    Here you need to register your plugins at the server

    :param cbpi: the cbpi core
    :return:
    '''

    cbpi.plugin.register("KettleStep", KettleStep)
    cbpi.plugin.register("PersonalPIDBoil", PersonalPIDBoil)
    cbpi.plugin.register("GroupSensor", GroupSensor)
    cbpi.plugin.register("CPUTemp", CPUTemp)
