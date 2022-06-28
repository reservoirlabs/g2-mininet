"""
Mininet host node extension.
"""
import os
import pty
import select
import docker
from mininet.node import Docker, Host
from mininet.log import info, debug, error

class DynamicDocker(Docker):
    """
    Dynamically attach a created docker container to as a Mininet host.
    """

    def __init__(self, name, cname=None,
                 **kwargs):
        self.name = name
        self.cname = cname if cname else name

        # setup docker client
        # self.dcli = docker.APIClient(base_url='unix://var/run/docker.sock')
        self.d_client = docker.from_env()
        self.dcli = self.d_client.api

        self._dc = self.d_client.containers.get(self.cname)
        self.dc = self._dc.id
        self.dcinfo = self.dcli.inspect_container(self.dc)
        self.did = self.dcinfo.get("Id")
        self.dcmd = self.dcinfo.get("Path")

        self.dimage = self.dcinfo.get('Config', {}).get('Image')

        hc = self.dcinfo.get('HostConfig')

        # keep resource in a dict for easy update during container lifetime
        self.resources = dict(
            cpu_quota=hc.get('CpuQuota'),
            cpu_period=hc.get('CpuPeriod'),
            cpu_shares=hc.get('CpuPercent'),
            cpuset_cpus=hc.get('CpusetCpus'),
            mem_limit=hc.get('Memory'),
            memswap_limit=hc.get('MemorySwap')
        )

        # for DEBUG
        debug("Created docker container object %s\n" % name)
        debug("image: %s\n" % str(self.dimage))
        debug("dcmd: %s\n" % str(self.dcmd))
        info("%s: kwargs %s\n" % (name, str(kwargs)))

        # call original Node.__init__
        Host.__init__(self, name, **kwargs)

        self.master = None
        self.slave = None

    def start(self):
        # Containernet ignores the CMD field of the Dockerfile.
        # Lets try to load it here and manually execute it once the
        # container is started and configured by Containernet:
        if not self._is_container_running():
            self.dcli.start(self.dc)
        info("{}: running container\n".format(self.name))

    def terminate( self ):
        """ Cleanup mininet host """
        self.cleanup()

    # Command support via shell process in namespace
    def startShell( self, *args, **kwargs ):
        "Start a shell process for running commands"
        if self.shell:
            error( "%s: shell is already running\n" % self.name )
            return
        # mnexec: (c)lose descriptors, (d)etach from tty,
        # (p)rint pid, and run in (n)amespace
        # opts = '-cd' if mnopts is None else mnopts
        # if self.inNamespace:
        #     opts += 'n'
        # bash -i: force interactive
        # -s: pass $* to shell, and make process easy to find in ps
        # prompt is set to sentinel chr( 127 )
        cmd = [ 'docker', 'exec', '-it',  self.did, 'env', 'PS1=' + chr( 127 ),
                'bash', '--norc', '-is', 'mininet:' + self.name ]
        # Spawn a shell subprocess in a pseudo-tty, to disable buffering
        # in the subprocess and insulate it from signals (e.g. SIGINT)
        # received by the parent
        self.master, self.slave = pty.openpty()
        self.shell = self._popen( cmd, stdin=self.slave, stdout=self.slave, stderr=self.slave,
                                  close_fds=False )
        self.stdin = os.fdopen( self.master, 'r' )
        self.stdout = self.stdin
        self.pid = self._get_pid()
        self.pollOut = select.poll()
        self.pollOut.register( self.stdout )
        # Maintain mapping between file descriptors and nodes
        # This is useful for monitoring multiple nodes
        # using select.poll()
        self.outToNode[ self.stdout.fileno() ] = self
        self.inToNode[ self.stdin.fileno() ] = self
        self.execed = False
        self.lastCmd = None
        self.lastPid = None
        self.readbuf = ''
        # Wait for prompt
        while True:
            data = self.read( 1024 )
            if data[ -1 ] == chr( 127 ):
                break
            self.pollOut.poll()
        self.waiting = False
        # +m: disable job control notification
        self.cmd( 'unset HISTFILE; stty -echo; set +m' )

    def popen( self, *args, **kwargs ):
        """Return a Popen() object in node's namespace
           args: Popen() args, single list, or string
           kwargs: Popen() keyword args"""
        if not self._is_container_running():
            error( "ERROR: Can't connect to Container \'%s\'' for docker host \'%s\'!\n" % (self.did, self.name) )
            return
        mncmd = ["docker", "exec", "-t", self.did]
        return Host.popen( self, *args, mncmd=mncmd, **kwargs )

