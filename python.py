# -*- coding: utf-8 -*-
# MitsubishiAirConditioner
#
# Author: itpansic
#

"""
<plugin key="MitsubishiAirConditioner" name="MitsubishiAirConditioner" author="itpansic" version="1.0.0" wikilink="https://github.com/itpansic/MitsubishiAirConditioner">
    <description>
        <h2>MitsubishiAirConditioner</h2><br/>
        Plugin to control one or more MitsubishiAirConditioner<br/>
        Each MitsubishiAirConditioner connects to a MAC-CCS-01M, all MAC-CCS-01M connects to one Modbus RTU - Modbus TCP router (Model Name:USR-N520) running on the TCP SERVER mode.
        <h3>Configuration</h3>
            <li>IP Address: IP address of TCP SERVER on Modbus RTU - Modbus TCP router</li>
            <li>Port: Port of TCP SERVER on RModbus RTU - Modbus TCP router</li>
            <li>Code Of MAC-CCS-01M (HEX): seperated by , or | or space</li>

    </description>
    <params>
        <param field="Address" label="IP Address" width="200px"/>
        <param field="Port" label="Port" width="50px" required="true" default="24"/>
        <param field="Mode1" label="Code Of MAC-CCS-01M (HEX)" width="180px" required="true" default="0x01"/>
        <param field="Mode2" label="Debug" width="75px">
            <options>
                <option label="True" value="Debug"/>
                <option label="False" value="Normal"  default="true" />
            </options>
        </param>
    </params>
</plugin>
"""

import time
import re
import Domoticz
import sys
version = '{}.{}'.format(sys.version_info.major, sys.version_info.minor)
path = '/usr/local/lib/python{}/dist-packages'.format(version)
sys.path.append(path)
from pyModbusTCP.client import ModbusClient


# 空调控制类，每个对象对应一个MAC-CCS-01M (即对应1个/组空调室内机)
class LJAircon:
    # 空调是否连通
    online = False

    # 其管理的开关
    dicDevice = None

    # 本对象的Unit偏移
    unitShift = 0

    # 设备 - 开关       寄存器地址偏移 0
    devicePowerOn = None
    # 设备 - 运行模式   寄存器地址偏移 1
    deviceMode = None
    # 设备 - 风速       寄存器地址偏移 2
    deviceFanSpeed = None
    # 设备 - 目标温度   寄存器地址偏移 3
    deviceSetPoint = None
    # 设备 - 室温   寄存器地址偏移 4
    deviceRoomPoint = None
    # 设备 - 风向       寄存器地址偏移 5
    deviceFanDirect = None
    # 设备 - 错误代码   寄存器地址偏移 6
    deviceFaultCode = None

    # 模块编码
    code = None

    def __init__(self, code):
        self.code = code
        self.dicDevice = {}

    # 更新设备状态为未在线
    def offline(self):
        if self.online:
            self.online = False
            Domoticz.Log('Aircon 0x{} offline now!'.format(self.address))

        for unit, device in self.dicDevice.items():
            UpdateDevice(Unit=unit, nValue=device.nValue, sValue=device.sValue, TimedOut=1, updateAnyway=False)


class BasePlugin:
    # 自上次读取一次新信息起经过了多久
    lastRefreshTimestamp = time.time()

    client = None

    # 待发送的需要等待回复的命令，成员格式为:{"code":"XX", "cmd":"XXXXXXX", "type": "query", "timestamp": timestamp} 
    arrayCmdNeedWait = []

    # 正在等待回应的命令
    dicCmdWaiting = None

    # 存储各个空调控制器的dic, key:字符串表示的控制器模块编码(HEX) value:LJAircon对象
    dicAircon = {}

    # 收到但仍未处理的数据字符串
    recv = ''

    # nValue/sValue至寄存器Payload的map - 开关
    mapVPPowerOn = None
    # 寄存器Payload至nValue/sValue的map - 开关
    mapPVPowerOn = None
    # nValue/sValue至寄存器Payload的map - 运行模式
    mapVPMode = None
    # 寄存器Payload至nValue/sValue的map - 运行模式
    mapPVMode = None
    # nValue/sValue至寄存器Payload的map - 风速
    mapVPFanSpeed = None
    # 寄存器Payload至nValue/sValue的map - 风速
    mapPVFanSpeed = None
    # nValue/sValue至寄存器Payload的map - 目标温度
    mapVPSetPoint = None
    # 寄存器Payload至nValue/sValue的map - 目标温度
    mapPVSetPoint = None
    # nValue/sValue至寄存器Payload的map - 室温
    mapVPRoomPoint = None
    # 寄存器Payload至nValue/sValue的map - 室温
    mapPVRoomPoint = None
    # nValue/sValue至寄存器Payload的map - 风向
    mapVPFanDirect = None
    # 寄存器Payload至nValue/sValue的map - 风向
    mapPVFanDirect = None

    def __init__(self):
        self.recv = ''
        # 0:关1:开
        self.mapVPPowerOn = {0:0, 1:1}
        self.mapPVPowerOn = self.revertDic(self.mapVPPowerOn)
        # 0:自动，1:制冷，2:送风，3:除湿，4:制热
        self.mapVPMode = {'10':0, '20':1, '30':2, '40':3, '50':4}
        self.mapPVMode = self.revertDic(self.mapVPMode)
        # 0:自动，2:低，3:中2，5:中1，6:高
        self.mapVPFanSpeed = {'10':0, '20':2, '30':3, '40':5, '50':6}
        self.mapPVFanSpeed = self.revertDic(self.mapVPFanSpeed)
        # 16~31°C (x10)，最小单位0.5°C 
        self.mapPVSetPoint = {}
        for i in range(160, 315, 5):
            self.mapPVSetPoint[i] = str((int((i - 160) / 5) + 1) * 10)
        self.mapVPSetPoint = self.revertDic(self.mapPVSetPoint)
        # 10~38°C (x10)，最小单位1°C
        self.mapPVRoomPoint = {}
        for i in range(100, 385, 5):
            if i%10 == 0:
                self.mapPVRoomPoint[i] = str(i // 10) + '°C'
            elif i%5 == 0:
                self.mapPVRoomPoint[i] = str(float(i) / 10) + '°C'
        self.mapVPRoomPoint = self.revertDic(self.mapPVRoomPoint)
        # 0:自动，1~5:位置1~5 7:摆风
        self.mapVPFanDirect = {'10':0, '20':1, '30':2, '40':3, '50':4, '60':5, '70':7}
        self.mapPVFanDirect = self.revertDic(self.mapVPFanDirect)
        return

    def onStart(self):
        Domoticz.Heartbeat(5)
        if Parameters["Mode2"] == "Debug":
            Domoticz.Debugging(1)
        else:
            Domoticz.Debugging(0)

        # 从Domoticz重新加载硬件和设备信息
        self.reloadFromDomoticz()

        if self.client and self.client.is_open():
            self.client.close()
        self.client = ModbusClient(host=Parameters["Address"], port=Parameters["Port"], unit_id = int(0xDC), auto_open=True, timeout=1)
        self.queryStatus()
        

    def onStop(self):
        Domoticz.Log("onStop called")

    def onConnect(self, Connection, Status, Description):
        Domoticz.Log("onConnect called")

    def onMessage(self, Connection, Data):
        Domoticz.Log("onMessage called")

    def clientConnected(self):
        if not self.client: return False
            
        if self.client.is_open():
            return True
        elif not self.client.open():
            Domoticz.Log('Warning: Modbus connect failed')
            return False

    def queryStatus(self):

        if not self.clientConnected():
            for aircon in self.dicAircon.values():
                aircon.offline()
            return
        
        for aircon in self.dicAircon.values():
            #if not aircon.online:
                #continue
            # 设备已连接才发送查询指令
            
            dicOptions = aircon.devicePowerOn.Options
            if not dicOptions or 'LJCode' not in dicOptions or 'LJShift' not in dicOptions: 
                return

            registerText = dicOptions['LJCode'] + dicOptions['LJShift']
            regs = self.client.read_holding_registers(int(registerText, 16), 7)
            if not regs or len(regs) != 7:
                Domoticz.Log('Warning: Reading Regs Fail! 0x' + dicOptions['LJCode'])
                aircon.online = False
                aircon.offline()
                continue
            # Domoticz.Log('Receive Regs:' + str(regs))
            if aircon.devicePowerOn and regs[0] in self.mapPVPowerOn:
                nValue = self.mapPVPowerOn[regs[0]]
                sValue = aircon.devicePowerOn.sValue
                device = aircon.devicePowerOn
                UpdateDevice(Unit=int(device.Options['LJUnit']), nValue=nValue, sValue=sValue, TimedOut=0)

            if aircon.deviceMode and regs[1] in self.mapPVMode:
                nValue = 1
                sValue = self.mapPVMode[regs[1]]
                device = aircon.deviceMode
                UpdateDevice(Unit=int(device.Options['LJUnit']), nValue=nValue, sValue=sValue, TimedOut=0)
            
            if aircon.deviceFanSpeed and regs[2] in self.mapPVFanSpeed:
                nValue = 1
                sValue = self.mapPVFanSpeed[regs[2]]
                device = aircon.deviceFanSpeed
                UpdateDevice(Unit=int(device.Options['LJUnit']), nValue=nValue, sValue=sValue, TimedOut=0)
            
            if aircon.deviceSetPoint and regs[3] in self.mapPVSetPoint:
                nValue = 1
                sValue = self.mapPVSetPoint[regs[3]]
                device = aircon.deviceSetPoint
                UpdateDevice(Unit=int(device.Options['LJUnit']), nValue=nValue, sValue=sValue, TimedOut=0)

            if aircon.deviceRoomPoint and regs[4] in self.mapPVRoomPoint:
                nValue = 1
                sValue = self.mapPVRoomPoint[regs[4]]
                device = aircon.deviceRoomPoint
                UpdateDevice(Unit=int(device.Options['LJUnit']), nValue=nValue, sValue=sValue, TimedOut=0)
            
            if aircon.deviceFanDirect and regs[5] in self.mapPVFanDirect:
                nValue = 1
                sValue = self.mapPVFanDirect[regs[5]]
                device = aircon.deviceFanDirect
                UpdateDevice(Unit=int(device.Options['LJUnit']), nValue=nValue, sValue=sValue, TimedOut=0)

            if aircon.deviceFaultCode:
                nValue = 1
                hexText = str(hex(regs[6]))
                if len(hexText)>= 4 and hexText[-4:] == '8000':
                    sValue = '运行正常'
                else:
                    sValue = '错误!故障代码: '+ hexText
                device = aircon.deviceFaultCode
                UpdateDevice(Unit=int(device.Options['LJUnit']), nValue=nValue, sValue=sValue, TimedOut=0)
                    
    def onCommand(self, Unit, Command, Level, Hue):
        if not self.clientConnected():
            Domoticz.Log('Modbus connect failed!')
            for aircon in self.dicAircon.values():
                aircon.offline()
            return
        Domoticz.Log(
            "onCommand called for Unit " + str(Unit) + ": Parameter '" + str(Command) + "', Level: " + str(Level))
        Command = Command.strip()
        action, sep, params = Command.partition(' ')
        action = action.capitalize()
        params = params.capitalize()
        device = Devices[Unit]
        options = device.Options
        if not options or 'LJCode' not in options or 'LJShift' not in options or 'LJUnit' not in options:
            return
        code = device.Options['LJCode']
        shift = device.Options['LJShift']

        if not code or code not in self.dicAircon or not shift or int(shift) < 0 or int(shift) > 6:
            return
        aircon = self.dicAircon[code]
        #if not aircon.online: 
            #return

        if shift == '00':
            # 开关
            if action == 'On':
                nValue = 1
            elif action == 'Off':
                nValue = 0
            self.sendCmdByNValue(aircon, self.mapVPPowerOn, aircon.devicePowerOn, nValue)

        elif shift == '01':
            # 模式
            if action == 'Set' and params == 'Level':
                if aircon.devicePowerOn.nValue == 0:
                    # 关机状态，先开机 #TODO测试连续写
                    self.sendCmdByNValue(aircon, self.mapVPPowerOn, aircon.devicePowerOn, 1)
                self.sendCmdBySValue(aircon, self.mapVPMode, aircon.deviceMode, str(Level))
        elif shift == '02':
            # 风速
            if action == 'Set' and params == 'Level':
                if aircon.devicePowerOn.nValue == 0:
                    # 关机状态，先开机
                    self.sendCmdByNValue(aircon, self.mapVPPowerOn, aircon.devicePowerOn, 1)
                self.sendCmdBySValue(aircon, self.mapVPFanSpeed, aircon.deviceFanSpeed, str(Level))
        elif shift == '03':
            # 温度
            if action == 'Set' and params == 'Level':
                if aircon.devicePowerOn.nValue == 0:
                    # 关机状态，先开机
                    self.sendCmdByNValue(aircon, self.mapVPPowerOn, aircon.devicePowerOn, 1)
                self.sendCmdBySValue(aircon, self.mapVPSetPoint, aircon.deviceSetPoint, str(Level))
        elif shift == '04':
            # 室温
            if action == 'Set' and params == 'Level':
                if aircon.devicePowerOn.nValue == 0:
                    # 关机状态，先开机
                    self.sendCmdByNValue(aircon, self.mapVPPowerOn, aircon.devicePowerOn, 1)
                self.sendCmdBySValue(aircon, self.mapVPRoomPoint, aircon.deviceRoomPoint, str(Level))
        elif shift == '05':
            # 风向
            if action == 'Set' and params == 'Level':
                if aircon.devicePowerOn.nValue == 0:
                    # 关机状态，先开机
                    self.sendCmdByNValue(aircon, self.mapVPPowerOn, aircon.devicePowerOn, 1)
                self.sendCmdBySValue(aircon, self.mapVPFanDirect, aircon.deviceFanDirect, str(Level))

    # 从sValue取值，找Payload，并写寄存器
    def sendCmdBySValue(self, aircon, mapVP, device, sValue):
        if not self.clientConnected(): return
        Domoticz.Log('sendCmdBySValue\(mapVP={}, device={}, sValue={}'.format(mapVP,device,sValue)) # TODO
        if not device or not mapVP or sValue not in mapVP:
            return None
        registerText = device.Options['LJCode'] + device.Options['LJShift']
        if (self.client.write_single_register(int(registerText, 16), mapVP[str(sValue)])):
            Domoticz.Log('write_single_register\(0x{}, {}\) success!'.format(registerText, mapVP[sValue])) # TODO
            timedOut = 0
            result = True
        else:
            Domoticz.Log('write_single_register\(0x{}, {}\) failed!'.format(registerText, mapVP[sValue])) # TODO
            timedOut = 1
            result = False
            aircon.offline()
            sValue = device.sValue
        
        UpdateDevice(Unit=int(device.Options['LJUnit']), nValue=device.nValue, sValue=str(sValue), TimedOut=timedOut)
        return result
    
    # 从nValue取值，找Payload，并写寄存器
    def sendCmdByNValue(self, aircon, mapVP, device, nValue):
        if not self.clientConnected(): return
        Domoticz.Log('sendCmdByNValue\(mapVP={}, device={}, nValue={}'.format(mapVP,device,nValue)) # TODO
        if not device or not mapVP or nValue not in mapVP:
            return None
        registerText = device.Options['LJCode'] + device.Options['LJShift']
        if (self.client.write_single_register(int(registerText, 16), mapVP[nValue])):
            Domoticz.Log('write_single_register\(0x{}, {}\) success!'.format(registerText, mapVP[nValue])) # TODO
            timedOut = 0
            result = True
        else:
            Domoticz.Log('write_single_register\(0x{}, {}\) failed!'.format(registerText, mapVP[nValue])) # TODO
            timedOut = 1
            result = False
            aircon.offline()
            nValue = device.nValue
        UpdateDevice(Unit=int(device.Options['LJUnit']), nValue=nValue, sValue=str(device.sValue), TimedOut=timedOut)
        return result

    def onNotification(self, Name, Subject, Text, Status, Priority, Sound, ImageFile):
        Domoticz.Log("Notification: " + Name + "," + Subject + "," + Text + "," + Status + "," + str(
            Priority) + "," + Sound + "," + ImageFile)

    def onDisconnect(self, Connection):
        Domoticz.Log("onDisconnect called")

    def onHeartbeat(self):
        Domoticz.Log('onHeartbeat Called ---------------------------------------')
        # 如果没连接则尝试重新连接 
        if not self.clientConnected():
            for aircon in self.dicAircon.values():
                aircon.offline()
            return

        # 查询空调状态
        self.queryStatus()

    def reloadFromDomoticz(self):
        self.dicAircon = {}
        strListCode = Parameters["Mode1"]
        strListCode = strListCode.replace(',', '')
        strListCode = strListCode.replace('|', '')
        strListCode = strListCode.replace(' ', '')
        strListCode = strListCode.replace('0X', '0x')
        strListCode = strListCode.replace('X', '0x')
        setCode = set(strListCode.split('0x'))
        setCode2 = set([])
        for tmp in setCode:
            if not tmp:
                continue
            setCode2.add(tmp.upper())
        for tmp2 in setCode2:
            if not tmp2:
                continue
            if len(tmp2) > 2: tmp2 = tmp2[-2:]
            tmp2 = '{:0>2}'.format(tmp2)
            Domoticz.Log('Detected Code:' + tmp2)
            self.dicAircon[tmp2] = LJAircon(tmp2)
        

        # 记录已有的unit
        setUnit = set([])
        # 待删除的device对应的unit
        setUnitDel = set([])
        # 所有的Unit集合
        setUnitAll = set(range(1, 256)) 
        # 将Device放入对应的控制器对象中，多余的device删除
        for unit in Devices:
            device = Devices[unit]
            dicOptions = device.Options
            Domoticz.Log("DEVICE FROM PANEL " + descDevice(device=device, unit=unit))

            shouldDelete = False
            if dicOptions and 'LJCode' in dicOptions and 'LJShift' in dicOptions and dicOptions['LJCode'] in self.dicAircon:
                # 有匹配的控制器，赋值
                aircon = self.dicAircon[dicOptions['LJCode']]
                if dicOptions['LJShift'] == '00':
                    # 开关
                    if aircon.devicePowerOn:
                        #已经有现成的设备，加入待删除
                        Domoticz.Log('Already have devicePowerOn, add to delete list. ' + device.Name)
                        shouldDelete = True
                    else:
                        aircon.devicePowerOn = device
                        aircon.dicDevice[unit] = device
                elif dicOptions['LJShift'] == '01':
                    # 运行模式
                    if aircon.deviceMode:
                        #已经有现成的设备，加入待删除
                        Domoticz.Log('Already have deviceMode, add to delete list. ' + device.Name)
                        shouldDelete = True
                    else:
                        aircon.deviceMode = device
                        aircon.dicDevice[unit] = device
                elif dicOptions['LJShift'] == '02':
                    # 风速
                    if aircon.deviceFanSpeed:
                        #已经有现成的设备，加入待删除
                        Domoticz.Log('Already have deviceFanSpeed, add to delete list. ' + device.Name)
                        shouldDelete = True
                    else:
                        aircon.deviceFanSpeed = device
                        aircon.dicDevice[unit] = device
                elif dicOptions['LJShift'] == '03':
                    # 目标温度
                    if aircon.deviceSetPoint:
                        #已经有现成的设备，加入待删除
                        Domoticz.Log('Already have deviceSetPoint, add to delete list. ' + device.Name)
                        shouldDelete = True
                    else:
                        aircon.deviceSetPoint = device
                        aircon.dicDevice[unit] = device
                elif dicOptions['LJShift'] == '04':
                    # 室温
                    if aircon.deviceRoomPoint:
                        #已经有现成的设备，加入待删除
                        Domoticz.Log('Already have deviceRoomPoint, add to delete list. ' + device.Name)
                        shouldDelete = True
                    else:
                        aircon.deviceRoomPoint = device
                        aircon.dicDevice[unit] = device
                elif dicOptions['LJShift'] == '05':
                    # 风向
                    if aircon.deviceFanDirect:
                        #已经有现成的设备，加入待删除
                        Domoticz.Log('Already have deviceFanDirect, add to delete list. ' + device.Name)
                        shouldDelete = True
                    else:
                        aircon.deviceFanDirect = device
                        aircon.dicDevice[unit] = device
                elif dicOptions['LJShift'] == '06':
                    # 状态
                    if aircon.deviceFaultCode:
                        #已经有现成的设备，加入待删除
                        Domoticz.Log('Already have deviceFaultCode, add to delete list. ' + device.Name)
                        shouldDelete = True
                    else:
                        aircon.deviceFaultCode = device
                        aircon.dicDevice[unit] = device
                else:
                    shouldDelete = True
            else:
                shouldDelete = True

            if shouldDelete:
                setUnitDel.add(unit)
            else:
                setUnit.add(unit)
        Domoticz.Log("DELETE DEVICES IN UNIT: " + str(setUnitDel))

        # 删除多余的Device
        for unit in setUnitDel:
            Devices[unit].Delete()

        # Check if images are in database
        #if "LJCountDown" not in Images:
        #    Domoticz.Image("LJCountDown.zip").Create()
        #image = Images["LJCountDown"].ID 

        # 遍历控制器，补全控制器对应的device
        for aircon in self.dicAircon.values():
            setAvariable = setUnitAll.difference(setUnit)
            if not setAvariable or len(setAvariable) == 0:
                continue
            if not aircon.devicePowerOn:
                newUnit = setAvariable.pop()
                setUnit.add(newUnit)
                optionsCustom = {"LJUnit": str(newUnit), 'LJCode' : aircon.code, 'LJShift' : '00'}
                name = '0x{} 开关'.format(aircon.code)
                aircon.devicePowerOn = Domoticz.Device(Name=name, Unit=newUnit, Type=244,Subtype=73, Switchtype=0, Options=optionsCustom)
                aircon.devicePowerOn.Create()
                aircon.dicDevice[newUnit] = aircon.devicePowerOn
                Domoticz.Log('ADD DEVICE :'+ descDevice(device=aircon.devicePowerOn, unit=newUnit))
            if not aircon.deviceMode and len(setAvariable) > 0:
                newUnit = setAvariable.pop()
                setUnit.add(newUnit)
                optionsCustom = {"LJUnit": str(newUnit), 'LJCode' : aircon.code, 'LJShift' : '01'}
                levelNames = 'Off|自动|制冷|送风|除湿|制热'
                optionsGradient = {'LevelActions': '|' * levelNames.count('|'),
                                'LevelNames': levelNames,
                                'LevelOffHidden': 'true',
                                'SelectorStyle': '0'}
                name = '0x{} 模式'.format(aircon.code)
                aircon.deviceMode = Domoticz.Device(Name=name, Unit=newUnit, TypeName="Selector Switch", Switchtype=18, Image=0, Options=dict(optionsCustom, **optionsGradient))
                aircon.deviceMode.Create()
                aircon.dicDevice[newUnit] = aircon.deviceMode
                Domoticz.Log('ADD DEVICE :'+ descDevice(device=aircon.deviceMode, unit=newUnit))
            if not aircon.deviceFanSpeed and len(setAvariable) > 0:
                newUnit = setAvariable.pop()
                setUnit.add(newUnit)
                optionsCustom = {"LJUnit": str(newUnit), 'LJCode' : aircon.code, 'LJShift' : '02'}
                levelNames = 'Off|自动|低|中2|中1|高'
                optionsGradient = {'LevelActions': '|' * levelNames.count('|'),
                                'LevelNames': levelNames,
                                'LevelOffHidden': 'true',
                                'SelectorStyle': '0'}
                name = '0x{} 风速'.format(aircon.code)
                aircon.deviceFanSpeed = Domoticz.Device(Name=name, Unit=newUnit, TypeName="Selector Switch", Switchtype=18, Image=0, Options=dict(optionsCustom, **optionsGradient))
                aircon.deviceFanSpeed.Create()
                aircon.dicDevice[newUnit] = aircon.deviceFanSpeed
                Domoticz.Log('ADD DEVICE :'+ descDevice(device=aircon.deviceFanSpeed, unit=newUnit))
            if not aircon.deviceSetPoint and len(setAvariable) > 0:
                newUnit = setAvariable.pop()
                setUnit.add(newUnit)
                optionsCustom = {"LJUnit": str(newUnit), 'LJCode' : aircon.code, 'LJShift' : '03'}
                levelNames = 'Off'
                for i in range(160, 315, 5):
                    if i%10 == 0:
                        levelNames += '|' + str(i // 10) + '℃'
                    elif i%5 == 0:
                        levelNames += '|' + str(float(i) / 10) + '℃'
                
                optionsGradient = {'LevelActions': '|' * levelNames.count('|'),
                                'LevelNames': levelNames,
                                'LevelOffHidden': 'true',
                                'SelectorStyle': '1'}
                name = '0x{} 设定温度'.format(aircon.code)
                aircon.deviceSetPoint = Domoticz.Device(Name=name, Unit=newUnit, TypeName="Selector Switch", Switchtype=18, Image=0, Options=dict(optionsCustom, **optionsGradient))
                aircon.deviceSetPoint.Create()
                aircon.dicDevice[newUnit] = aircon.deviceSetPoint
                Domoticz.Log('ADD DEVICE :'+ descDevice(device=aircon.deviceSetPoint, unit=newUnit))
            if not aircon.deviceRoomPoint and len(setAvariable) > 0:
                newUnit = setAvariable.pop()
                setUnit.add(newUnit)
                optionsCustom = {"LJUnit": str(newUnit), 'LJCode' : aircon.code, 'LJShift' : '04'}
                name = '0x{} 室温'.format(aircon.code)
                aircon.deviceRoomPoint = Domoticz.Device(Name=name, Unit=newUnit, TypeName="Text", Image=17,  Options=optionsCustom)
                aircon.deviceRoomPoint.Create()
                aircon.dicDevice[newUnit] = aircon.deviceRoomPoint
                Domoticz.Log('ADD DEVICE :'+ descDevice(device=aircon.deviceRoomPoint, unit=newUnit))
            if not aircon.deviceFanDirect and len(setAvariable) > 0:
                newUnit = setAvariable.pop()
                setUnit.add(newUnit)
                optionsCustom = {"LJUnit": str(newUnit), 'LJCode' : aircon.code, 'LJShift' : '05'}
                levelNames = 'Off|自动|位置1|位置2|位置3|位置4|位置5|摆风'
                optionsGradient = {'LevelActions': '|' * levelNames.count('|'),
                                'LevelNames': levelNames,
                                'LevelOffHidden': 'true',
                                'SelectorStyle': '0'}
                name = '0x{} 风向'.format(aircon.code)
                aircon.deviceFanDirect = Domoticz.Device(Name=name, Unit=newUnit, TypeName="Selector Switch", Switchtype=18, Image=0, Options=dict(optionsCustom, **optionsGradient))
                aircon.deviceFanDirect.Create()
                aircon.dicDevice[newUnit] = aircon.deviceFanDirect
                Domoticz.Log('ADD DEVICE :'+ descDevice(device=aircon.deviceFanDirect, unit=newUnit))
            if not aircon.deviceFaultCode and len(setAvariable) > 0:
                newUnit = setAvariable.pop()
                setUnit.add(newUnit)
                optionsCustom = {"LJUnit": str(newUnit), 'LJCode' : aircon.code, 'LJShift' : '06'}
                name = '0x{} 状态'.format(aircon.code)
                aircon.deviceRoomPoint = Domoticz.Device(Name=name, Unit=newUnit, TypeName="Text", Image=17,  Options=optionsCustom)
                aircon.deviceRoomPoint.Create()
                aircon.dicDevice[newUnit] = aircon.deviceRoomPoint
                Domoticz.Log('ADD DEVICE :'+ descDevice(device=aircon.deviceRoomPoint, unit=newUnit))

    def revertDic(self, dic):
        if dic:
            return {v : k for k, v in dic.items()}
        return None

global _plugin
_plugin = BasePlugin()

def UpdateDevice(Unit, nValue, sValue, TimedOut=0, updateAnyway=True):
    # Make sure that the Domoticz device still exists (they can be deleted) before updating it  
    if (Unit in Devices):
        if updateAnyway or (Devices[Unit].nValue != nValue) or (Devices[Unit].sValue != sValue) or (Devices[Unit].TimedOut != TimedOut):
            Devices[Unit].Update(nValue=nValue, sValue=str(sValue), TimedOut=TimedOut)
            # Domoticz.Log("UPDATE DEVICE "+ descDevice(Devices[Unit], unit=Unit, nValue=nValue, sValue=sValue))
    return

def logConnectStatus(conn):
    if conn:
        Domoticz.Log('~~~~~~~~~~~Connecting: ' + str(conn.Connecting()) + ' Connected: ' + str(conn.Connected()))

def descDevice(device, unit=None, nValue = None, sValue = None):
    if not device: return ''
    n = nValue if nValue else device.nValue
    s = sValue if sValue else device.sValue
    code = 'XX' if 'LJCode' not in device.Options else device.Options['LJCode']
    shift = 'XX' if 'LJShift' not in device.Options else device.Options['LJShift']
    return 'Unit: {}, Name: {}, nValue: {}, sValue: {}, TimedOut: {} Aircon: 0x{:0>2}, Shift: {}'.format(unit, device.Name, n, s, device.TimedOut, code, shift)

def onStart():
    global _plugin
    _plugin.onStart()

def onStop():
    global _plugin
    _plugin.onStop()

def onConnect(Connection, Status, Description):
    global _plugin
    _plugin.onConnect(Connection, Status, Description)

def onMessage(Connection, Data):
    global _plugin
    _plugin.onMessage(Connection, Data)

def onCommand(Unit, Command, Level, Hue):
    global _plugin
    _plugin.onCommand(Unit, Command, Level, Hue)

def onNotification(Name, Subject, Text, Status, Priority, Sound, ImageFile):
    global _plugin
    _plugin.onNotification(Name, Subject, Text, Status, Priority, Sound, ImageFile)

def onDisconnect(Connection):
    global _plugin
    _plugin.onDisconnect(Connection)

def onHeartbeat():
    global _plugin
    _plugin.onHeartbeat()

# Generic helper functions
def DumpConfigToLog():
    for x in Parameters:
        if Parameters[x] != "":
            Domoticz.Debug("'" + x + "':'" + str(Parameters[x]) + "'")
    Domoticz.Debug("Device count: " + str(len(Devices)))
    for x in Devices:
        Domoticz.Debug("Device:           " + str(x) + " - " + str(Devices[x]))
        Domoticz.Debug("Device ID:       '" + str(Devices[x].ID) + "'")
        Domoticz.Debug("Device Name:     '" + Devices[x].Name + "'")
        Domoticz.Debug("Device nValue:    " + str(Devices[x].nValue))
        Domoticz.Debug("Device sValue:   '" + Devices[x].sValue + "'")
        Domoticz.Debug("Device LastLevel: " + str(Devices[x].LastLevel))
    return
