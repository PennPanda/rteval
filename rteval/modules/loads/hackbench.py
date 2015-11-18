#
#   hackbench.py - class to manage an instance of hackbench load
#
#   Copyright 2009 - 2013   Clark Williams <williams@redhat.com>
#   Copyright 2009 - 2013   David Sommerseth <davids@redhat.com>
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 2 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License along
#   with this program; if not, write to the Free Software Foundation, Inc.,
#   51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
#   For the avoidance of doubt the "preferred form" of this code is one which
#   is in an open unpatent encumbered format. Where cryptographic key signing
#   forms part of the process of creating an executable the information
#   including keys needed to generate an equivalently functional executable
#   are deemed to be part of the source code.
#

import sys, os, time, glob, subprocess, errno, os.path
from signal import SIGKILL
from rteval.modules.loads import CommandLineLoad
from rteval.Log import Log
from rteval.misc import expand_cpulist

class Hackbench(CommandLineLoad):
    def __init__(self, config, logger):
        CommandLineLoad.__init__(self, "hackbench", config, logger)


    def _WorkloadSetup(self):
        'calculate arguments based on input parameters'
        (mem, units) = self.memsize
        if units == 'KB':
            mem = mem / (1024.0 * 1024.0)
        elif units == 'MB':
            mem = mem / 1024.0
        elif units == 'TB':
            mem = mem * 1024
        ratio = float(mem) / float(self.num_cpus)
        if ratio >= 0.75:
            mult = float(self._cfg.setdefault('jobspercore', 2))
        else:
            self._log(Log.INFO, "Low memory system (%f GB/core)! Not running" % ratio)
            mult = 0
            self._donotrun = True

        # figure out how many nodes we have
        self.nodes = [ n.split('/')[-1][4:] for n in glob.glob('/sys/devices/system/node/node*') ]

        # get the cpus for each node
        self.cpus = {}
        for n in self.nodes:
            self.cpus[n] = [ int(c.split('/')[-1][3:]) for c in glob.glob('/sys/devices/system/node/node%s/cpu[0-9]*' % n) ]
            self.cpus[n].sort()

        # figure out if we can use numactl or have to use taskset
        self.__usenumactl = False
        self.__multinodes = False
        if len(self.nodes) > 1:
            self.__multinodes = True
            self._log(Log.INFO, "running with multiple nodes (%d)" % len(self.nodes))
            if os.path.exists('/usr/bin/numactl'):
                self.__usenumactl = True
                self._log(Log.INFO, "using numactl for thread affinity")

        self.args = ['hackbench',  '-P',
                     '-g', str(self.jobs),
                     '-l', str(self._cfg.setdefault('loops', '100')),
                     '-s', str(self._cfg.setdefault('datasize', '100'))
                     ]
        self.__err_sleep = 5.0


    def _WorkloadBuild(self):
        # Nothing to build, so we're basically ready
        self._setReady()


    def _WorkloadPrepare(self):
        self.__nullfp = os.open("/dev/null", os.O_RDWR)
        if self._logging:
            self.__out = self.open_logfile("hackbench.stdout")
            self.__err = self.open_logfile("hackbench.stderr")
        else:
            self.__out = self.__err = self.__nullfp

        self.tasks = {}

        self._log(Log.DEBUG, "starting loop (jobs: %d)" % self.jobs)


    def __starton(self, node):
        if self.__multinodes:
            if self.__usenumactl:
                args = [ 'numactl', '--cpubindnode', node ] + self.args
            else:
                cpulist = ",".join([ str(n) for n in self.cpus[node] ])
                args = ['taskset', '-c', cpulist ] + self.args
        else:
            args = self.args

        self._log(Log.DEBUG, "starting on node %s: args = %s" % (node, args))
        return subprocess.Popen(args,
                                stdin=self.__nullfp,
                                stdout=self.__out,
                                stderr=self.__err)

    def _WorkloadTask(self):
        if self.shouldStop():
            return

        for n in self.nodes:
            if not self.tasks.has_key(n) or self.tasks[n].poll() is not None:
                try:
                    self.tasks[n] = self.__starton(n)
                except OSError, e:
                    if e.errno != errno.ENOMEM:
                        raise e
                    # Catch out-of-memory errors and wait a bit to (hopefully)
                    # ease memory pressure
                    self._log(Log.DEBUG, "ERROR: %s, sleeping for %f seconds" % (e.strerror, self.__err_sleep))
                    time.sleep(self.__err_sleep)
                    if self.__err_sleep < 60.0:
                        self.__err_sleep *= 2.0
                    if self.__err_sleep > 60.0:
                        self.__err_sleep = 60.0


    def WorkloadAlive(self):
        # As hackbench is short-lived, lets pretend it is always alive
        return True


    def _WorkloadCleanup(self):
        if self._donotrun:
            return

        for node in self.nodes:
            if self.tasks.has_key(node) and self.tasks[node].poll() is None:
                self._log(Log.INFO, "cleaning up hackbench on node %s" % node)
                self.tasks[node].send_signal(SIGKILL)
                if self.tasks[node].poll() == None:
                    time.sleep(2)
            self.tasks[node].wait()
            del self.tasks[node]

        os.close(self.__nullfp)
        if self._logging:
            os.close(self.__out)
            del self.__out
            os.close(self.__err)
            del self.__err

        del self.__nullfp



def ModuleParameters():
    return {"jobspercore": {"descr": "Number of working threads per CPU core",
                            "default": 5,
                            "metavar": "NUM"},
            }



def create(config, logger):
    return Hackbench(config, logger)


if __name__ == '__main__':
    h = Hackbench(params={'debugging':True, 'verbose':True})
    h.run()
