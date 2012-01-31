#!/usr/bin/python2.6
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2010  Joanna Rutkowska <joanna@invisiblethingslab.com>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#
#

import sys
import os
from PyQt4.QtCore import *
from PyQt4.QtGui import *

from qubes.qubes import QubesVmCollection
from qubes.qubes import QubesException
from qubes.qubes import qubes_store_filename
from qubes.qubes import QubesVmLabels
from qubes.qubes import dry_run
from qubes.qubes import qubes_guid_path
from qubes.qubes import QubesDaemonPidfile
from qubes.qubes import QubesHost
from qubes import qubesutils

import qubesmanager.resources_rc
import ui_newappvmdlg
from ui_mainwindow import *
from appmenu_select import AppmenuSelectWindow
from settings import VMSettingsWindow
from restore import RestoreVMsWindow
from backup import BackupVMsWindow

from firewall import EditFwRulesDlg, QubesFirewallRulesModel

from pyinotify import WatchManager, Notifier, ThreadedNotifier, EventsCodes, ProcessEvent

import subprocess
import time
import threading
from datetime import datetime,timedelta

updates_stat_file = 'last_update.stat'
qubes_guid_path = '/usr/bin/qubes_guid'

update_suggestion_interval = 14 # 14 days

class QubesConfigFileWatcher(ProcessEvent):
    def __init__ (self, update_func):
        self.update_func = update_func

    def process_IN_MODIFY (self, event):
        self.update_func()

class VmStatusIcon(QLabel):
    def __init__(self, vm, parent=None):
        super (VmStatusIcon, self).__init__(parent)
        self.vm = vm
        (icon_pixmap, icon_sz) = self.set_vm_icon(self.vm)
        self.setPixmap (icon_pixmap)
        self.setFixedSize (icon_sz)
        self.previous_power_state = vm.last_power_state

    def update(self):
        if self.previous_power_state != self.vm.last_power_state:
            (icon_pixmap, icon_sz) = self.set_vm_icon(self.vm)
            self.setPixmap (icon_pixmap)
            self.setFixedSize (icon_sz)
            self.previous_power_state = self.vm.last_power_state

    def set_vm_icon(self, vm):
        if vm.qid == 0:
            icon = QIcon (":/dom0.png")
        elif vm.is_appvm():
            icon = QIcon (vm.label.icon_path)
        elif vm.is_template():
            icon = QIcon (":/templatevm.png")
        elif vm.is_netvm():
            icon = QIcon (":/netvm.png")
        else:
            icon = QIcon()

        icon_sz = QSize (VmManagerWindow.row_height * 0.8, VmManagerWindow.row_height * 0.8)
        if vm.last_power_state:
            icon_pixmap = icon.pixmap(icon_sz)
        else:
            icon_pixmap = icon.pixmap(icon_sz, QIcon.Disabled)

        return (icon_pixmap, icon_sz)


class VmInfoWidget (QWidget):

    def __init__(self, vm, parent = None):
        super (VmInfoWidget, self).__init__(parent)

        layout = QHBoxLayout ()

        self.label_name = QLabel (vm.name)
        self.vm_icon = VmStatusIcon(vm)

        layout.addWidget(self.vm_icon)
        layout.addSpacing (10)
        layout.addWidget(self.label_name, alignment=Qt.AlignLeft)

        self.setLayout(layout)
        
    def update_vm_state (self, vm):
        self.vm_icon.update()

   


class VmTemplateWidget (QWidget):
    def __init__(self, vm, parent=None):
        super(VmTemplateWidget, self).__init__(parent)
        
        layout = QVBoxLayout()
        if vm.template_vm is not None:
            self.label_tmpl = QLabel ("<font color=\"black\">" + (vm.template_vm.name) + "</font>")
        else:
            if vm.is_appvm(): # and vm.template_vm is None
                self.label_tmpl = QLabel ("<i><font color=\"gray\">StandaloneVM</i></font>")
            elif vm.is_template():
                self.label_tmpl = QLabel ("<i><font color=\"gray\">TemplateVM</i></font>")
            elif vm.qid == 0:
                self.label_tmpl = QLabel ("<i><font color=\"gray\">AdminVM</i></font>")
            elif vm.is_netvm():
                self.label_tmpl = QLabel ("<i><font color=\"gray\">NetVM</i></font>")
            else:
                self.label_tmpl = QLabel ("<i><font color=\"gray\">---</i></font>")


        layout.addWidget(self.label_tmpl, alignment=Qt.AlignHCenter)

        self.setLayout(layout)



class VmIconWidget (QWidget):
    def __init__(self, icon_path, enabled=True, parent=None):
        super(VmIconWidget, self).__init__(parent)

        label_icon = QLabel()
        icon = QIcon (icon_path)
        icon_sz = QSize (VmManagerWindow.row_height * 0.8, VmManagerWindow.row_height * 0.3)
        icon_pixmap = icon.pixmap(icon_sz, QIcon.Disabled if not enabled else QIcon.Normal)
        label_icon.setPixmap (icon_pixmap)
        label_icon.setFixedSize (icon_sz)
        
        layout = QVBoxLayout()
        layout.addWidget(label_icon)
        self.setLayout(layout)


class VmNetvmWidget (QWidget):
    def __init__(self, vm, parent=None):
        super(VmNetvmWidget, self).__init__(parent)

        layout = QHBoxLayout()
        self.icon = VmIconWidget(":/networking.png", vm.is_networked())
        
        if vm.is_netvm():
            self.label_nvm = QLabel ("<font color=\"black\">self</font>")
        elif vm.netvm_vm is not None:
            self.label_nvm = QLabel ("<font color=\"black\">" + (vm.netvm_vm.name) + "</font>")
        else:
            self.label_nvm = QLabel ("<font color=\"black\">None</font>")

        layout.addWidget(self.icon, alignment=Qt.AlignLeft)
        layout.addWidget(self.label_nvm, alignment=Qt.AlignHCenter)
        self.setLayout(layout)
            


class VmUsageBarWidget (QWidget):
    def __init__(self, min, max, format, label, update_func, vm, load, parent = None):
        super (VmUsageBarWidget, self).__init__(parent)

        self.min = min
        self.max = max
        self.update_func = update_func

        self.widget = QProgressBar()
        self.widget.setMinimum(min)
        self.widget.setMaximum(max)
        self.widget.setFormat(format);
        self.label = QLabel(label)

        layout = QHBoxLayout()
        layout.addWidget(self.label)
        layout.addWidget(self.widget)

        self.setLayout(layout)

        self.update_load(vm, load)

    def update_load(self, vm, load):
        self.widget.setValue(self.update_func(vm, load))


class LoadChartWidget (QWidget):

    def __init__(self, vm, cpu_load = 0, parent = None):
        super (LoadChartWidget, self).__init__(parent)
        self.load = cpu_load if vm.last_power_state else 0
        assert self.load >= 0 and self.load <= 100, "load = {0}".format(self.load)
        self.load_history = [self.load]

    def update_load (self, vm, cpu_load):
        self.load = cpu_load if vm.last_power_state else 0
        assert self.load >= 0, "load = {0}".format(self.load)
        # assert self.load >= 0 and self.load <= 100, "load = {0}".format(self.load)
        if self.load > 100:
            # FIXME: This is an ugly workaround :/
            self.load = 100

        self.load_history.append (self.load)
        self.repaint()

    def paintEvent (self, Event = None):
        p = QPainter (self)
        dx = 4

        W = self.width()
        H = self.height() - 5
        N = len(self.load_history)
        if N > W/dx:
            tail = N - W/dx
            N = W/dx
            self.load_history = self.load_history[tail:]

        assert len(self.load_history) == N

        for i in range (0, N-1):
            val = self.load_history[N- i - 1]
            hue = 200
            sat = 70 + val*(255-70)/100
            color = QColor.fromHsv (hue, sat, 255)
            pen = QPen (color)
            pen.setWidth(dx-1)
            p.setPen(pen)
            if val > 0:
                p.drawLine (W - i*dx - dx, H , W - i*dx - dx, H - (H - 5) * val/100)

class MemChartWidget (QWidget):

    def __init__(self, vm, parent = None):
        super (MemChartWidget, self).__init__(parent)
        self.load = vm.get_mem()*100/qubes_host.memory_total if vm.last_power_state else 0
        assert self.load >= 0 and self.load <= 100, "mem = {0}".format(self.load)
        self.load_history = [self.load]

    def update_load (self, vm):
        self.load = vm.get_mem()*100/qubes_host.memory_total if vm.last_power_state else 0
        assert self.load >= 0 and self.load <= 100, "load = {0}".format(self.load)
        self.load_history.append (self.load)
        self.repaint()

    def paintEvent (self, Event = None):
        p = QPainter (self)
        dx = 4

        W = self.width()
        H = self.height() - 5
        N = len(self.load_history)
        if N > W/dx:
            tail = N - W/dx
            N = W/dx
            self.load_history = self.load_history[tail:]

        assert len(self.load_history) == N

        for i in range (0, N-1):
            val = self.load_history[N- i - 1]
            hue = 120
            sat = 70 + val*(255-70)/100
            color = QColor.fromHsv (hue, sat, 255)
            pen = QPen (color)
            pen.setWidth(dx-1)
            p.setPen(pen)
            if val > 0:
                p.drawLine (W - i*dx - dx, H , W - i*dx - dx, H - (H - 5) * val/100)


class VmUpdateInfoWidget(QWidget):

    def __init__(self, vm, parent = None):
        super (VmUpdateInfoWidget, self).__init__(parent)
        layout = QHBoxLayout ()
        self.label = QLabel("---")
        layout.addWidget(self.label, alignment=Qt.AlignCenter)
        self.setLayout(layout)

        self.previous_outdated = False
        self.previous_update_recommended = False

    def update_outdated(self, vm):
        outdated = vm.is_outdated()
        if outdated and not self.previous_outdated:
            self.label.setText("<font color=\"red\">outdated</font>")
        
        self.previous_outdated = outdated
        if vm.is_updateable():
            update_recommended = self.previous_update_recommended
            stat_file = vm.dir_path + '/' + updates_stat_file
            if not os.path.exists(stat_file) or \
                time.time() - os.path.getmtime(stat_file) > \
                update_suggestion_interval * 24 * 3600:
                    update_recommended = True
            else:
                update_recommended = False
                self.label.setText("<font color=\"green\">OK</font>")
            if update_recommended and not self.previous_update_recommended:
                self.label.setText("<font color=\"#CCCC00\">check updates</font>")
            self.previous_update_recommended = update_recommended


class VmBlockDevicesWidget(QWidget):
    def __init__(self, vm, parent=None):
        super(VmBlockDevicesWidget, self).__init__(parent)

        combo = QComboBox()
        combo.addItem("USB dummy1")
        combo.addItem("USB dummy2")
        combo.addItem("USB dummy3")

        layout = QVBoxLayout()
        layout.addWidget(combo)
        self.setLayout(layout)


class VmRowInTable(object):
    def __init__(self, vm, row_no, table):
        self.vm = vm
        self.row_no = row_no

        table.setRowHeight (row_no, VmManagerWindow.row_height)

        self.info_widget = VmInfoWidget(vm)
        table.setCellWidget(row_no, 0, self.info_widget)

        self.template_widget = VmTemplateWidget(vm)
        table.setCellWidget(row_no, 1, self.template_widget)

        self.netvm_widget = VmNetvmWidget(vm)
        table.setCellWidget(row_no, 2, self.netvm_widget)

        self.cpu_usage_widget = VmUsageBarWidget(0, 100, "", "CPU", 
                            lambda vm, val: val if vm.last_power_state else 0, vm, 0)
        table.setCellWidget(row_no, 3, self.cpu_usage_widget)

        self.load_widget = LoadChartWidget(vm)
        table.setCellWidget(row_no, 4, self.load_widget)

        self.mem_usage_widget = VmUsageBarWidget(0, qubes_host.memory_total/1024, "%v MB", "MEM", 
                            lambda vm, val: vm.get_mem()/1024 if vm.last_power_state else 0, vm, 0)
        table.setCellWidget(row_no, 5, self.mem_usage_widget)

        self.mem_widget = MemChartWidget(vm)
        table.setCellWidget(row_no, 6, self.mem_widget)
 
        self.updateinfo_widget = VmUpdateInfoWidget(vm)
        table.setCellWidget(row_no, 7, self.updateinfo_widget)

        self.blockdevices_widget = VmBlockDevicesWidget(vm)
        table.setCellWidget(row_no, 8, self.blockdevices_widget)


    def update(self, counter, cpu_load = None):
        self.info_widget.update_vm_state(self.vm)
        if cpu_load is not None:
            self.cpu_usage_widget.update_load(self.vm, cpu_load)
            self.mem_usage_widget.update_load(self.vm, None)
            self.load_widget.update_load(self.vm, cpu_load)
            self.mem_widget.update_load(self.vm)
            self.updateinfo_widget.update_outdated(self.vm)

class NewAppVmDlg (QDialog, ui_newappvmdlg.Ui_NewAppVMDlg):
    def __init__(self, parent = None):
        super (NewAppVmDlg, self).__init__(parent)
        self.setupUi(self)

vm_shutdown_timeout = 15000 # in msec

class VmShutdownMonitor(QObject):
    def __init__(self, vm):
        self.vm = vm

    def check_if_vm_has_shutdown(self):
        vm = self.vm
        vm_start_time = vm.get_start_time()
        if not vm.is_running() or (vm_start_time and vm_start_time >= datetime.utcnow() - timedelta(0,vm_shutdown_timeout/1000)):
            if vm.is_template():
                trayIcon.showMessage ("Qubes Manager", "You have just modified template '{0}'. You should now restart all the VMs based on it, so they could see the changes.".format(vm.name), msecs=8000)
            return

        reply = QMessageBox.question(None, "VM Shutdown",
                                     "The VM <b>'{0}'</b> hasn't shutdown within the last {1} seconds, do you want to kill it?<br>".format(vm.name, vm_shutdown_timeout/1000),
                                     "Kill it!", "Wait another {0} seconds...".format(vm_shutdown_timeout/1000))

        if reply == 0:
            vm.force_shutdown()
        else:
            QTimer.singleShot (vm_shutdown_timeout, self.check_if_vm_has_shutdown)

class ThreadMonitor(QObject):
    def __init__(self):
        self.success = True
        self.error_msg = None
        self.event_finished = threading.Event()

    def set_error_msg(self, error_msg):
        self.success = False
        self.error_msg = error_msg
        self.set_finished()

    def is_finished(self):
        return self.event_finished.is_set()

    def set_finished(self):
        self.event_finished.set()


class VmManagerWindow(Ui_VmManagerWindow, QMainWindow):
    row_height = 30
    max_visible_rows = 7
    update_interval = 1000 # in msec
    show_inactive_vms = True
    columns_indices = { "Name": 0,
                        "Template": 1,
                        "NetVM": 2,
                        "CPU": 3,
                        "CPU Graph": 4,
                        "MEM": 5,
                        "MEM Graph": 6,
                        "Update Info": 7,
                        "Block Device": 8 }



    def __init__(self, parent=None):
        super(VmManagerWindow, self).__init__()
        self.setupUi(self)
        self.toolbar = self.toolBar
        
        self.qvm_collection = QubesVmCollection()
        
        self.connect(self.table, SIGNAL("itemSelectionChanged()"), self.table_selection_changed)
        
        cur_pos = self.pos()
        self.table.setColumnWidth(0, 200)
        self.setSizeIncrement(QtCore.QSize(200, 30))
        self.centralwidget.setSizeIncrement(QtCore.QSize(200, 30))
        self.table.setSizeIncrement(QtCore.QSize(200, 30))
        self.fill_table()
        self.move(cur_pos)
            
        self.table.setColumnHidden( self.columns_indices["NetVM"], True)
        self.actionNetVM.setChecked(False)
        self.table.setColumnHidden( self.columns_indices["CPU Graph"], True)
        self.actionCPU_Graph.setChecked(False)
        self.table.setColumnHidden( self.columns_indices["MEM Graph"], True)
        self.actionMEM_Graph.setChecked(False)
        self.table.setColumnHidden( self.columns_indices["Block Device"], True)
        self.actionBlock_Devices.setChecked(False)

        self.update_table_columns()
        self.set_table_geom_height()

        self.counter = 0
        self.shutdown_monitor = {}
        self.last_measure_results = {}
        self.last_measure_time = time.time()
        QTimer.singleShot (self.update_interval, self.update_table)

    def set_table_geom_height(self):
        minH =  self.table.horizontalHeader().height() + \
                2*self.table.contentsMargins().top() +\
                self.centralwidget.layout().contentsMargins().top() +\
                self.centralwidget.layout().contentsMargins().bottom() 
                #self.table.contentsMargins().bottom()  # this is huge, dunno why
                #2*self.centralwidget.layout().verticalSpacing() # and this is negative...

        #All this sizing is kind of magic, so change it only if you have to
        #or if you know what you're doing :)
               
        n = self.table.rowCount();

        if n > self.max_visible_rows:
            for i in range (0, self.max_visible_rows):
                minH += self.table.rowHeight(i)
            maxH = minH
            for i in range (self.max_visible_rows, n):
                maxH += self.table.rowHeight(i)
        else:
            for i in range (n):
                minH += self.table.rowHeight(i)
            maxH = minH

        self.centralwidget.setMinimumHeight(minH)
        maxH += self.menubar.height() + self.statusbar.height() +\
                self.toolbar.height()
        self.setMaximumHeight(maxH)
        self.adjustSize()


    def get_vms_list(self):
        self.qvm_collection.lock_db_for_reading()
        self.qvm_collection.load()
        self.qvm_collection.unlock_db()

        vms_list = [vm for vm in self.qvm_collection.values()]
        for vm in vms_list:
            vm.last_power_state = vm.is_running()

        no_vms = len (vms_list)
        vms_to_display = []

        # First, the NetVMs...
        for netvm in vms_list:
            if netvm.is_netvm():
                vms_to_display.append (netvm)

        # Now, the templates...
        for tvm in vms_list:
            if tvm.is_template():
                vms_to_display.append (tvm)

        label_list = QubesVmLabels.values()
        label_list.sort(key=lambda l: l.index)
        for label in [label.name for label in label_list]:
            for appvm in [vm for vm in vms_list if ((vm.is_appvm() or vm.is_disposablevm()) and vm.label.name == label)]:
                vms_to_display.append(appvm)

        assert len(vms_to_display) == no_vms
        return vms_to_display

    def fill_table(self):
        #self.table.clear()
        vms_list = self.get_vms_list()
        self.table.setRowCount(len(vms_list))

        vms_in_table = []

        row_no = 0
        for vm in vms_list:
            if (not self.show_inactive_vms) and (not vm.last_power_state):
                continue
            if vm.internal:
                continue
            vm_row = VmRowInTable (vm, row_no, self.table)
            vms_in_table.append (vm_row)
            row_no += 1

        self.table.setRowCount(row_no)
        self.vms_list = vms_list
        self.vms_in_table = vms_in_table
        self.reload_table = False


    def mark_table_for_update(self):
        self.reload_table = True

    # When calling update_table() directly, always use out_of_schedule=True!
    def update_table(self, out_of_schedule=False):

        if manager_window.isVisible():
            some_vms_have_changed_power_state = False
            for vm in self.vms_list:
                state = vm.is_running();
                if vm.last_power_state != state:
                    vm.last_power_state = state
                    some_vms_have_changed_power_state = True

            if self.reload_table or ((not self.show_inactive_vms) and some_vms_have_changed_power_state): 
                self.fill_table()

            if self.counter % 3 == 0 or out_of_schedule:
                (self.last_measure_time, self.last_measure_results) = \
                    qubes_host.measure_cpu_usage(self.last_measure_results,
                    self.last_measure_time)

                for vm_row in self.vms_in_table:
                    cur_cpu_load = None
                    if vm_row.vm.get_xid() in self.last_measure_results:
                        cur_cpu_load = self.last_measure_results[vm_row.vm.xid]['cpu_usage']
                    else:
                        cur_cpu_load = 0
                    vm_row.update(self.counter, cpu_load = cur_cpu_load)
            else:
                for vm_row in self.vms_in_table:
                    vm_row.update(self.counter)

            #self.table_selection_changed()

        if not out_of_schedule:
            self.counter += 1
            QTimer.singleShot (self.update_interval, self.update_table)

    def update_table_columns(self):

        width = self.table.horizontalHeader().length() +\
                self.table.verticalScrollBar().width() +\
                self.centralwidget.layout().contentsMargins().left() +\
                self.centralwidget.layout().contentsMargins().right()

        self.table.setFixedWidth( width )

    def table_selection_changed (self):

        vm = self.get_selected_vm()

        # Update available actions:
        self.action_settings.setEnabled(True)
        self.action_removevm.setEnabled(not vm.installed_by_rpm and not vm.last_power_state)
        self.action_resumevm.setEnabled(not vm.last_power_state)
        self.action_pausevm.setEnabled(vm.last_power_state and vm.qid != 0)
        self.action_shutdownvm.setEnabled(not vm.is_netvm() and vm.last_power_state and vm.qid != 0)
        self.action_appmenus.setEnabled(not vm.is_netvm())
        self.action_editfwrules.setEnabled(vm.is_networked() and not (vm.is_netvm() and not vm.is_proxyvm()))
        self.action_updatevm.setEnabled(vm.is_updateable() or vm.qid == 0)


    def closeEvent (self, event):
        if event.spontaneous(): # There is something borked in Qt, as the logic here is inverted on X11
            self.hide()
            event.ignore()

    @pyqtSlot(name='on_action_createvm_triggered')
    def action_createvm_triggered(self):
        dialog = NewAppVmDlg()

        print "Create VM triggered!\n"

        # Theoretically we should be locking for writing here and unlock
        # only after the VM creation finished. But the code would be more messy...
        # Instead we lock for writing in the actual worker thread

        self.qvm_collection.lock_db_for_reading()
        self.qvm_collection.load()
        self.qvm_collection.unlock_db()

        label_list = QubesVmLabels.values()
        label_list.sort(key=lambda l: l.index)
        for (i, label) in enumerate(label_list):
            dialog.vmlabel.insertItem(i, label.name)
            dialog.vmlabel.setItemIcon (i, QIcon(label.icon_path))

        template_vm_list = [vm for vm in self.qvm_collection.values() if not vm.internal and vm.is_template()]

        default_index = 0
        for (i, vm) in enumerate(template_vm_list):
            if vm is self.qvm_collection.get_default_template_vm():
                default_index = i
                dialog.template_name.insertItem(i, vm.name + " (default)")
            else:
                dialog.template_name.insertItem(i, vm.name)
        dialog.template_name.setCurrentIndex(default_index)

        dialog.vmname.selectAll()
        dialog.vmname.setFocus()

        if dialog.exec_():
            vmname = str(dialog.vmname.text())
            if self.qvm_collection.get_vm_by_name(vmname) is not None:
                QMessageBox.warning (None, "Incorrect AppVM Name!", "A VM with the name <b>{0}</b> already exists in the system!".format(vmname))
                return

            label = label_list[dialog.vmlabel.currentIndex()]
            template_vm = template_vm_list[dialog.template_name.currentIndex()]

            allow_networking = dialog.allow_networking.isChecked()

            thread_monitor = ThreadMonitor()
            thread = threading.Thread (target=self.do_create_appvm, args=(vmname, label, template_vm, allow_networking, thread_monitor))
            thread.daemon = True
            thread.start()

            progress = QProgressDialog ("Creating new AppVM <b>{0}</b>...".format(vmname), "", 0, 0)
            progress.setCancelButton(None)
            progress.setModal(True)
            progress.show()

            while not thread_monitor.is_finished():
                app.processEvents()
                time.sleep (0.1)

            progress.hide()

            if thread_monitor.success:
                trayIcon.showMessage ("Qubes Manager", "VM '{0}' has been created.".format(vmname), msecs=3000)
            else:
                QMessageBox.warning (None, "Error creating AppVM!", "ERROR: {0}".format(thread_monitor.error_msg))


    def do_create_appvm (self, vmname, label, template_vm, allow_networking, thread_monitor):
        vm = None
        try:
            self.qvm_collection.lock_db_for_writing()
            self.qvm_collection.load()

            vm = self.qvm_collection.add_new_appvm(vmname, template_vm, label = label)
            vm.create_on_disk(verbose=False)
            firewall = vm.get_firewall_conf()
            firewall["allow"] = allow_networking
            firewall["allowDns"] = allow_networking
            vm.write_firewall_conf(firewall)
            self.qvm_collection.save()
        except Exception as ex:
            thread_monitor.set_error_msg (str(ex))
            if vm:
                vm.remove_from_disk()
        finally:
            self.qvm_collection.unlock_db()

        thread_monitor.set_finished()


    def get_selected_vm(self):
        row_index = self.table.currentRow()
        assert self.vms_in_table[row_index] is not None
        vm = self.vms_in_table[row_index].vm
        return vm

    @pyqtSlot(name='on_action_removevm_triggered')
    def action_removevm_triggered(self):
        vm = self.get_selected_vm()
        assert not vm.is_running()
        assert not vm.installed_by_rpm

        self.qvm_collection.lock_db_for_reading()
        self.qvm_collection.load()
        self.qvm_collection.unlock_db()
 
        if vm.is_template():
            dependent_vms = self.qvm_collection.get_vms_based_on(vm.qid)
            if len(dependent_vms) > 0:
                QMessageBox.warning (None, "Warning!",
                                     "This Template VM cannot be removed, because there is at least one AppVM that is based on it.<br>"
                                     "<small>If you want to remove this Template VM and all the AppVMs based on it,"
                                     "you should first remove each individual AppVM that uses this template.</small>")

                return

        reply = QMessageBox.question(None, "VM Removal Confirmation",
                                     "Are you sure you want to remove the VM <b>'{0}'</b>?<br>"
                                     "<small>All data on this VM's private storage will be lost!</small>".format(vm.name),
                                     QMessageBox.Yes | QMessageBox.Cancel)


        if reply == QMessageBox.Yes:

            thread_monitor = ThreadMonitor()
            thread = threading.Thread (target=self.do_remove_vm, args=(vm, thread_monitor))
            thread.daemon = True
            thread.start()

            progress = QProgressDialog ("Removing VM: <b>{0}</b>...".format(vm.name), "", 0, 0)
            progress.setCancelButton(None)
            progress.setModal(True)
            progress.show()

            while not thread_monitor.is_finished():
                app.processEvents()
                time.sleep (0.1)

            progress.hide()

            if thread_monitor.success:
                trayIcon.showMessage ("Qubes Manager", "VM '{0}' has been removed.".format(vm.name), msecs=3000)
            else:
                QMessageBox.warning (None, "Error removing VM!", "ERROR: {0}".format(thread_monitor.error_msg))

    def do_remove_vm (self, vm, thread_monitor):
        try:
            self.qvm_collection.lock_db_for_writing()
            self.qvm_collection.load()

            #TODO: the following two conditions should really be checked by qvm_collection.pop() overload...
            if vm.is_template() and qvm_collection.default_template_qid == vm.qid:
                qvm_collection.default_template_qid = None
            if vm.is_netvm() and qvm_collection.default_netvm_qid == vm.qid:
                qvm_collection.default_netvm_qid = None

            vm.remove_from_disk()
            self.qvm_collection.pop(vm.qid)
            self.qvm_collection.save()
        except Exception as ex:
            thread_monitor.set_error_msg (str(ex))
        finally:
            self.qvm_collection.unlock_db()

        thread_monitor.set_finished()

    @pyqtSlot(name='on_action_resumevm_triggered')
    def action_resumevm_triggered(self):
        vm = self.get_selected_vm()
        assert not vm.is_running()

        if vm.is_paused():
            try:
                subprocess.check_call (["/usr/sbin/xl", "unpause", vm.name])
            except Exception as ex:
                QMessageBox.warning (None, "Error unpausing VM!", "ERROR: {0}".format(ex))
            return

        thread_monitor = ThreadMonitor()
        thread = threading.Thread (target=self.do_start_vm, args=(vm, thread_monitor))
        thread.daemon = True
        thread.start()

        trayIcon.showMessage ("Qubes Manager", "Starting '{0}'...".format(vm.name), msecs=3000)

        while not thread_monitor.is_finished():
            app.processEvents()
            time.sleep (0.1)

        if thread_monitor.success:
            trayIcon.showMessage ("Qubes Manager", "VM '{0}' has been started.".format(vm.name), msecs=3000)
        else:
            QMessageBox.warning (None, "Error starting VM!", "ERROR: {0}".format(thread_monitor.error_msg))

    def do_start_vm(self, vm, thread_monitor):
        try:
            vm.verify_files()
            xid = vm.start()
        except Exception as ex:
            thread_monitor.set_error_msg(str(ex))
            thread_monitor.set_finished()
            return

        retcode = subprocess.call ([qubes_guid_path, "-d", str(xid), "-c", vm.label.color, "-i", vm.label.icon, "-l", str(vm.label.index)])
        if (retcode != 0):
            thread_monitor.set_error_msg("Cannot start qubes_guid!")

        thread_monitor.set_finished()
 
    @pyqtSlot(name='on_action_pausevm_triggered')
    def action_pausevm_triggered(self):
        vm = self.get_selected_vm()
        assert vm.is_running()
        try:
            subprocess.check_call (["/usr/sbin/xl", "pause", vm.name])
        except Exception as ex:
            QMessageBox.warning (None, "Error pausing VM!", "ERROR: {0}".format(ex))
            return

    @pyqtSlot(name='on_action_shutdownvm_triggered')
    def action_shutdownvm_triggered(self):
        vm = self.get_selected_vm()
        assert vm.is_running()

        reply = QMessageBox.question(None, "VM Shutdown Confirmation",
                                     "Are you sure you want to power down the VM <b>'{0}'</b>?<br>"
                                     "<small>This will shutdown all the running applications within this VM.</small>".format(vm.name),
                                     QMessageBox.Yes | QMessageBox.Cancel)

        app.processEvents()

        if reply == QMessageBox.Yes:
            try:
                subprocess.check_call (["/usr/sbin/xl", "shutdown", vm.name])
            except Exception as ex:
                QMessageBox.warning (None, "Error shutting down VM!", "ERROR: {0}".format(ex))
                return

            trayIcon.showMessage ("Qubes Manager", "VM '{0}' is shutting down...".format(vm.name), msecs=3000)
            self.shutdown_monitor[vm.qid] = VmShutdownMonitor (vm)
            QTimer.singleShot (vm_shutdown_timeout, self.shutdown_monitor[vm.qid].check_if_vm_has_shutdown)

    @pyqtSlot(name='on_action_settings_triggered')
    def action_settings_triggered(self):
        vm = self.get_selected_vm()
        settings_window = VMSettingsWindow(vm)
        settings_window.exec_()
   

    @pyqtSlot(name='on_action_appmenus_triggered')
    def action_appmenus_triggered(self):
        vm = self.get_selected_vm()
        select_window = AppmenuSelectWindow(vm)
        select_window.exec_()

    @pyqtSlot(name='on_action_updatevm_triggered')
    def action_updatevm_triggered(self):
        vm = self.get_selected_vm()

        if not vm.is_running():
            reply = QMessageBox.question(None, "VM Update Confirmation",
                    "VM need to be running for update. Do you want to start this VM?<br>",
                    QMessageBox.Yes | QMessageBox.Cancel)
            if reply != QMessageBox.Yes:
                return
            trayIcon.showMessage ("Qubes Manager", "Starting '{0}'...".format(vm.name), msecs=3000)

        app.processEvents()

        thread_monitor = ThreadMonitor()
        thread = threading.Thread (target=self.do_update_vm, args=(vm, thread_monitor))
        thread.daemon = True
        thread.start()

        while not thread_monitor.is_finished():
            app.processEvents()
            time.sleep (0.2)

        if vm.qid != 0:    
            if thread_monitor.success:
                # gpk-update-viewer was started, don't know if user installs updates, but touch stat file anyway
                open(vm.dir_path + '/' + updates_stat_file, 'w').close()
            else:
                QMessageBox.warning (None, "Error VM update!", "ERROR: {0}".format(thread_monitor.error_msg))

    def do_update_vm(self, vm, thread_monitor):
        try:
            if vm.qid == 0:
                subprocess.check_call (["/usr/bin/qvm-dom0-update", "--gui"])
            else:
                qubesutils.run_in_vm(vm, "user:gpk-update-viewer", verbose=False, autostart=True)
        except Exception as ex:
            thread_monitor.set_error_msg(str(ex))
            thread_monitor.set_finished()
            return
        thread_monitor.set_finished()

    @pyqtSlot(name='on_action_showallvms_triggered')
    def action_showallvms_triggered(self):
        self.show_inactive_vms = self.action_showallvms.isChecked()
        self.mark_table_for_update()
        self.update_table(out_of_schedule = True)
        self.set_table_geom_height()

    @pyqtSlot(name='on_action_editfwrules_triggered')
    def action_editfwrules_triggered(self):
        vm = self.get_selected_vm()
        dialog = EditFwRulesDlg()
        model = QubesFirewallRulesModel()
        model.set_vm(vm)
        dialog.set_model(model)

        if vm.netvm_vm is not None and not vm.netvm_vm.is_proxyvm():
            QMessageBox.warning (None, "VM configuration problem!", "The '{0}' AppVM is not network connected to a FirewallVM!<p>".format(vm.name) +\
                    "You may edit the '{0}' VM firewall rules, but these will not take any effect until you connect it to a working Firewall VM.".format(vm.name))

        if dialog.exec_():
            model.apply_rules()


    @pyqtSlot(name='on_action_restore_triggered')
    def action_restore_triggered(self):
        restore_window = RestoreVMsWindow()
        restore_window.exec_()

    @pyqtSlot(name='on_action_backup_triggered')
    def action_backup_triggered(self):
        backup_window = BackupVMsWindow()
        backup_window.exec_()



    def showhide_collumn(self, col_num, show):
        self.table.setColumnHidden( col_num, not show)
        self.update_table_columns()
        
    def on_actionTemplate_toggled(self, checked):
        self.showhide_collumn( 1, checked)

    def on_actionNetVM_toggled(self, checked):
        self.showhide_collumn( 2, checked)
    
    def on_actionCPU_toggled(self, checked):
        self.showhide_collumn( 3, checked)
    
    def on_actionCPU_Graph_toggled(self, checked):
        self.showhide_collumn( 4, checked)    

    def on_actionMEM_toggled(self, checked):
        self.showhide_collumn( 5, checked)   
    
    def on_actionMEM_Graph_toggled(self, checked):
        self.showhide_collumn( 6, checked)

    def on_actionUpdate_Info_toggled(self, checked):
        self.showhide_collumn( 7, checked)    

    def on_actionBlock_Devices_toggled(self, checked):
        self.showhide_collumn( 8, checked)    


class QubesTrayIcon(QSystemTrayIcon):
    def __init__(self, icon):
        QSystemTrayIcon.__init__(self, icon)
        self.menu = QMenu()

        action_showmanager = self.createAction ("Open VM Manager", slot=show_manager, icon="qubes")
        action_backup = self.createAction ("Make backup")
        action_preferences = self.createAction ("Preferences")
        action_set_netvm = self.createAction ("Set default NetVM", icon="networking")
        action_sys_info = self.createAction ("System Info", icon="dom0")
        action_exit = self.createAction ("Exit", slot=exit_app)

        action_backup.setDisabled(True)
        action_preferences.setDisabled(True)
        action_set_netvm.setDisabled(True)
        action_sys_info.setDisabled(True)

        self.addActions (self.menu, (action_showmanager, action_backup, action_sys_info, None, action_preferences, action_set_netvm, None, action_exit))

        self.setContextMenu(self.menu)

        self.connect (self, SIGNAL("activated (QSystemTrayIcon::ActivationReason)"), self.icon_clicked)

    def icon_clicked(self, reason):
        if reason == QSystemTrayIcon.Context:
            # Handle the right click normally, i.e. display the context menu
            return
        else:
            toggle_manager()

    def addActions(self, target, actions):
        for action in actions:
            if action is None:
                target.addSeparator()
            else:
                target.addAction(action)


    def createAction(self, text, slot=None, shortcut=None, icon=None,
                     tip=None, checkable=False, signal="triggered()"):
        action = QAction(text, self)
        if icon is not None:
            action.setIcon(QIcon(":/%s.png" % icon))
        if shortcut is not None:
            action.setShortcut(shortcut)
        if tip is not None:
            action.setToolTip(tip)
            action.setStatusTip(tip)
        if slot is not None:
            self.connect(action, SIGNAL(signal), slot)
        if checkable:
            action.setCheckable(True)
        return action


def show_manager():
    manager_window.show()

def toggle_manager():
    if manager_window.isVisible():
        manager_window.hide()
    else:
        manager_window.show()
        manager_window.update_table(True)

def exit_app():
    notifier.stop()
    app.exit()


# Bases on the original code by:
# Copyright (c) 2002-2007 Pascal Varet <p.varet@gmail.com>

def handle_exception( exc_type, exc_value, exc_traceback ):
    import sys
    import os.path
    import traceback

    filename, line, dummy, dummy = traceback.extract_tb( exc_traceback ).pop()
    filename = os.path.basename( filename )
    error    = "%s: %s" % ( exc_type.__name__, exc_value )

    QMessageBox.critical(None, "Houston, we have a problem...",
                         "Whoops. A critical error has occured. This is most likely a bug "
                         "in Qubes Manager.<br><br>"
                         "<b><i>%s</i></b>" % error +
                         "at <b>line %d</b> of file <b>%s</b>.<br/><br/>"
                         % ( line, filename ))

    #sys.exit(1)

def main():


    # Avoid starting more than one instance of the app
    lock = QubesDaemonPidfile ("qubes-manager")
    if lock.pidfile_exists():
        if lock.pidfile_is_stale():
            lock.remove_pidfile()
            print "Removed stale pidfile (has the previous daemon instance crashed?)."
        else:
            exit (0)

    lock.create_pidfile()

    global qubes_host
    qubes_host = QubesHost()

    global app
    app = QApplication(sys.argv)
    app.setOrganizationName("The Qubes Project")
    app.setOrganizationDomain("http://qubes-os.org")
    app.setApplicationName("Qubes VM Manager")
    app.setWindowIcon(QIcon(":/qubes.png"))

    sys.excepthook = handle_exception

    global manager_window
    manager_window = VmManagerWindow()
    wm = WatchManager()
    mask = EventsCodes.OP_FLAGS.get('IN_MODIFY')

    global notifier
    notifier = ThreadedNotifier(wm, QubesConfigFileWatcher(manager_window.mark_table_for_update))
    notifier.start()
    wdd = wm.add_watch(qubes_store_filename, mask)

    global trayIcon
    trayIcon = QubesTrayIcon(QIcon(":/qubes.png"))
    trayIcon.show()

    app.exec_()
    trayIcon = None

