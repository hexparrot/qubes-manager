#!/usr/bin/python2.6
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2012  Agnieszka Kostrzewa <agnieszka.kostrzewa@gmail.com>
# Copyright (C) 2012  Marek Marczykowski <marmarek@mimuw.edu.pl>
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
from qubes.qubes import QubesDaemonPidfile
from qubes.qubes import QubesHost

import qubesmanager.resources_rc

from pyinotify import WatchManager, Notifier, ThreadedNotifier, EventsCodes, ProcessEvent

import subprocess
import time
import threading
from operator import itemgetter

from ui_backupdlg import *
from multiselectwidget import *



class BackupVMsWindow(Ui_Backup, QWizard):

    def __init__(self, parent=None):
        super(BackupVMsWindow, self).__init__(parent)

        self.setupUi(self)

        self.selectVMsWidget = MultiSelectWidget(self)
        self.verticalLayout.insertWidget(1, self.selectVMsWidget)

        self.selectVMsWidget.available_list.addItem("netVM1")
        self.selectVMsWidget.available_list.addItem("appVM1")
        self.selectVMsWidget.available_list.addItem("appVM2")
        self.selectVMsWidget.available_list.addItem("templateVM1")
        
        self.connect(self, SIGNAL("currentIdChanged(int)"), self.current_page_changed)


       
    def reject(self):
        self.done(0)

    def save_and_apply(self):
        pass

    @pyqtSlot(name='on_selectPathButton_clicked')
    def selectPathButton_clicked(self):
        self.path = self.pathLineEdit.text()
        newPath = QFileDialog.getExistingDirectory(self, 'Select backup directory.')
        if newPath:
            self.pathLineEdit.setText(newPath)
            self.path = newPath

    def current_page_changed(self, id):
        self.button(self.CancelButton).setDisabled(id==3)
            

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
                         "in Qubes Restore VMs application.<br><br>"
                         "<b><i>%s</i></b>" % error +
                         "at <b>line %d</b> of file <b>%s</b>.<br/><br/>"
                         % ( line, filename ))




def main():

    global qubes_host
    qubes_host = QubesHost()

    global app
    app = QApplication(sys.argv)
    app.setOrganizationName("The Qubes Project")
    app.setOrganizationDomain("http://qubes-os.org")
    app.setApplicationName("Qubes Restore VMs")

    sys.excepthook = handle_exception

    qvm_collection = QubesVmCollection()
    qvm_collection.lock_db_for_reading()
    qvm_collection.load()
    qvm_collection.unlock_db()

    global backup_window
    backup_window = BackupVMsWindow()

    backup_window.show()

    app.exec_()
    app.exit()



if __name__ == "__main__":
    main()
