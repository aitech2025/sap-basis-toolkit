#!/bin/sh
# Builds a disposable SAP-like Linux host on THIS machine for collector integration tests (dev/CI only).
# Real OpenSSH server, a <sid>adm user with tcsh (as SAP installs it), the SAP file layout read by OS discovery,
# and the real Prometheus node_exporter. Run as root inside a throw-away container.
set -e
H=saplab
grep -q " $H" /etc/hosts || echo "127.0.0.1 $H $H.lab.test" >> /etc/hosts
id s4ladm >/dev/null 2>&1 || useradd -m -s /usr/bin/tcsh s4ladm
echo 's4ladm:Lab-pass-123' | chpasswd
echo 'root:Root-pass-123' | chpasswd
mkdir -p /run/sshd && ssh-keygen -A >/dev/null
cat > /etc/ssh/sshd_config.d/lab.conf <<CONF
PermitRootLogin yes
PasswordAuthentication yes
KbdInteractiveAuthentication no
CONF
pgrep -x sshd >/dev/null || /usr/sbin/sshd
# SAP layout: ABAP system S4L (ASCS01, D00) and HANA HDB (HDB02) on one host
mkdir -p /usr/sap/S4L/SYS/profile /sapmnt/S4L/profile /usr/sap/HDB/HDB02/exe /usr/sap/hostctrl/exe
cat > /usr/sap/sapservices <<SVC
#!/bin/sh
LD_LIBRARY_PATH=/usr/sap/S4L/ASCS01/exe:\$LD_LIBRARY_PATH; export LD_LIBRARY_PATH; /usr/sap/S4L/ASCS01/exe/sapstartsrv pf=/usr/sap/S4L/SYS/profile/S4L_ASCS01_$H -D -u s4ladm
systemctl --no-ask-password start SAPS4L_00 # sapstartsrv pf=/usr/sap/S4L/SYS/profile/S4L_D00_$H
systemctl --no-ask-password start SAPHDB_02 # sapstartsrv pf=/usr/sap/HDB/SYS/profile/HDB_HDB02_$H
SVC
chmod 755 /usr/sap/sapservices
cat > /sapmnt/S4L/profile/DEFAULT.PFL <<PFL
SAPSYSTEMNAME = S4L
SAPGLOBALHOST = $H
SAPDBHOST = $H
dbms/type = hdb
dbs/hdb/dbname = S4L
dbs/hdb/schema = SAPHANADB
rdisp/mshost = $H
PFL
printf 'release: 2.00.077.00\nfullversion: 2.00.077.00.1710325774\n' > /usr/sap/HDB/HDB02/exe/manifest
cat > /usr/sap/hostctrl/exe/saphostctrl <<'HC'
#!/bin/sh
[ "$2" = "ListDatabases" ] && echo "Instance name: HDB02, Database name: S4L, Database type: hdb, Database status: Running"
HC
chmod 755 /usr/sap/hostctrl/exe/saphostctrl
chmod -R a+rX /usr/sap /sapmnt
# a process named like the HANA index server, so the database process check has something real to find
cp /bin/sleep /tmp/hdbindexserver && (pgrep -x hdbindexserver >/dev/null || nohup /tmp/hdbindexserver 100000 >/dev/null 2>&1 &)
# real node_exporter
if [ ! -x /tmp/node_exporter/node_exporter ] && [ -f /tmp/ne.tgz ]; then
  mkdir -p /tmp/node_exporter && tar -xzf /tmp/ne.tgz -C /tmp/node_exporter --strip-components=1
fi
pgrep -x node_exporter >/dev/null || nohup /tmp/node_exporter/node_exporter --web.listen-address=127.0.0.1:9100 >/tmp/node_exporter.log 2>&1 &
echo "lab ready: ssh root|s4ladm@$H:22, node_exporter $H:9100"
