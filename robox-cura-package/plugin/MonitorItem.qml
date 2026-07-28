// Copyright (c) 2018 Ultimaker B.V.
// Cura is released under the terms of the LGPLv3 or higher.

import QtQuick 2.10
import QtQuick.Controls 2.0

import UM 1.2 as UM
import Cura 1.0 as Cura
Component
{
    Item
    {
        Rectangle
        {
            color: UM.Theme.getColor("main_background")

            anchors.right: parent.right
            width: parent.width * 0.3
            anchors.top: parent.top
            anchors.bottom: parent.bottom

            Cura.PrintMonitor
            {
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: printButton.top
                anchors.bottomMargin: UM.Theme.getSize("thick_margin").height
            }

            Cura.SecondaryButton
            {
                id: printButton
                anchors.bottom: coolDownButton.top
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.leftMargin: UM.Theme.getSize("default_margin").width
                anchors.rightMargin: UM.Theme.getSize("default_margin").width
                anchors.bottomMargin: UM.Theme.getSize("default_margin").height
                text: "Send to Printer"
                visible: Cura.MachineManager.printerOutputDevices.length > 0
                onClicked: {
                    var device = Cura.MachineManager.printerOutputDevices[0]
                    if (device) {
                        device.sendCommand("PRINT")
                    }
                }
            }

            Cura.SecondaryButton
            {
                id: coolDownButton
                anchors.bottom: openDoorButton.top
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.leftMargin: UM.Theme.getSize("default_margin").width
                anchors.rightMargin: UM.Theme.getSize("default_margin").width
                anchors.bottomMargin: UM.Theme.getSize("default_margin").height
                text: "Cool Down"
                visible: Cura.MachineManager.printerOutputDevices.length > 0
                onClicked: {
                    var device = Cura.MachineManager.printerOutputDevices[0]
                    if (device) {
                        device.sendCommand("M104 S0")
                        device.sendCommand("M140 S0")
                    }
                }
            }

            Cura.SecondaryButton
            {
                id: openDoorButton
                anchors.bottom: footerSeparator.top
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.leftMargin: UM.Theme.getSize("default_margin").width
                anchors.rightMargin: UM.Theme.getSize("default_margin").width
                anchors.bottomMargin: UM.Theme.getSize("default_margin").height
                text: "Open Door"
                enabled: {
                    if (Cura.MachineManager.printerOutputDevices.length < 1) return false
                    var device = Cura.MachineManager.printerOutputDevices[0]
                    var printer = device.activePrinter
                    if (!printer) return false
                    var nozzleHot = printer.extruders.length > 0 && printer.extruders[0].hotendTemperature > 50
                    var bedHot = printer.bedTemperature > 40
                    return !nozzleHot && !bedHot
                }
                visible: Cura.MachineManager.printerOutputDevices.length > 0
                onClicked: {
                    var device = Cura.MachineManager.printerOutputDevices[0]
                    if (device) {
                        device.sendCommand("G37 S")
                    }
                }
            }

            Rectangle
            {
                id: footerSeparator
                width: parent.width
                height: UM.Theme.getSize("wide_lining").height
                color: UM.Theme.getColor("wide_lining")
                anchors.bottom: monitorButton.top
                anchors.bottomMargin: UM.Theme.getSize("thick_margin").height
            }

            // MonitorButton is actually the bottom footer panel.
            Cura.MonitorButton
            {
                id: monitorButton
                anchors.bottom: parent.bottom
                anchors.left: parent.left
                anchors.right: parent.right
            }
        }
    }
}