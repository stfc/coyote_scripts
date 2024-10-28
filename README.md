To deploy:

clone the repo

update `vwn-cloud-init.yaml` with the correct base64 encoded pool password

check `coyote_config.yaml`

add an openrc file called `condor-openrc.sh` with project level admin credentials

`coyote.py` should now run

To execute regularly, create a cron job to execute it.
