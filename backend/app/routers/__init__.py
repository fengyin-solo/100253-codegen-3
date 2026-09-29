"""业务模块路由汇总。

这里统一按别名导入再暴露 ROUTERS：模块名有可能和内置名撞车（某个业务模块就叫 dict、list
这种名字时），按名字直接 import 会把内置类型覆盖掉，函数注解在运行时求值就会报
'module' object is not subscriptable。
"""
from __future__ import annotations

from app.routers import interlock as router_interlock
from app.routers import trackcircuit as router_trackcircuit
from app.routers import signal as router_signal
from app.routers import pointmachine as router_pointmachine
from app.routers import cable as router_cable
from app.routers import powersupply as router_powersupply
from app.routers import atp as router_atp
from app.routers import balise as router_balise
from app.routers import axlecounter as router_axlecounter
from app.routers import dispatchcenter as router_dispatchcenter
from app.routers import maintenancewindow as router_maintenancewindow
from app.routers import relay as router_relay
from app.routers import fuse as router_fuse
from app.routers import lightning as router_lightning
from app.routers import emergencyresp as router_emergencyresp
from app.routers import testrecord as router_testrecord
from app.routers import fault as router_fault
from app.routers import tool as router_tool
from app.routers import regulation as router_regulation
from app.routers import training as router_training
from app.routers import drawingarchive as router_drawingarchive

ROUTERS = [router_interlock, router_trackcircuit, router_signal, router_pointmachine, router_cable, router_powersupply, router_atp, router_balise, router_axlecounter, router_dispatchcenter, router_maintenancewindow, router_relay, router_fuse, router_lightning, router_emergencyresp, router_testrecord, router_fault, router_tool, router_regulation, router_training, router_drawingarchive]
