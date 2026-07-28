from . import RoboxOutputDevicePlugin


def getMetaData():
    return {}


def register(app):
    return {
        "output_device": RoboxOutputDevicePlugin.RoboxOutputDevicePlugin(),
    }
