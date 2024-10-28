#!/usr/bin/python
import json
import sys
import os
import paramiko
from time import gmtime, strftime
from subprocess import Popen, PIPE
from ConfigParser import SafeConfigParser
import math
from time import sleep

env = os.environ.copy()

# import all variables from the config file
with open('coyote_config.json') as config_file:
    config = json.load(config_file)

vwnStaticNamePrefix = config['vwnStaticNamePrefix']
vwnImageName = config['vwnImageName']
networkName = config['networkName']
availabilityZone = config['availabilityZone']
configDrive = config['configDrive']
userDataPath = config['userDataPath']
coyoteKeyPair = config['coyoteKeyPair']
coyotePrivateKeyPath = config['coyotePrivateKeyPath']
coyoteRemoteUser = config['coyoteRemoteUser']
jsonTrackingFile = config['jsonTrackingFile']
metadata = config['metadata']
aggregates = config['aggregates']
sourcecmd = config['sourcecmd']


# disables host
def disable_host(host):
    print(host)
    ssh = paramiko.SSHClient()
    pkey = paramiko.RSAKey.from_private_key_file(coyotePrivateKeyPath)
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=coyoteRemoteUser, pkey=pkey)
    ssh_stdin, ssh_stdout, ssh_stderr = ssh.exec_command("/usr/local/bin/condor_disable.sh")


# pass commands to be performed
def command_line(c):
    p = Popen(sourcecmd + c, shell=True, stdout=PIPE, env=env)
    return p.communicate()[0]


# check through vm's on condor project and delete any that are errored or have been shut down
def delete_errored_vm():
    # Check for virtual worker nodes which are down or errored and remove
    for node in nodelist:
        if vwnStaticNamePrefix in node["Name"]:
            ip_address = ""
            try:
                ip_address = node["Networks"].split("=")[1]
            except IndexError:
                print("Node has no IP")

            try:
                vwnJSON[node["Name"]]["Status"] = node["Status"]
                vwnJSON[node["Name"]]["IP Address"] = ip_address
                if node["Status"] == "ERROR" or node["Power State"] == "Shutdown":
                    print(node["Name"] + " - " + node["Power State"] + " - is virtual worker")
                    print("Deleting Instance: " + node["ID"])
                    command_line("openstack server delete " + node["ID"])
                    del vwnJSON[node["Name"]]
            except KeyError:
                print("node not known")
                command_line("openstack server delete " + node["ID"])
            except IndexError:
                print("node problem")


nodelist = json.loads(command_line("openstack server list --long -f json"))

flavorListPre = json.loads(command_line("openstack flavor list --long -f json"))
flavorDict = {}
for flavor in flavorListPre:
    flavorDict[flavor["Name"]] = flavor

instanceCreateString = "openstack server create --key-name " + coyoteKeyPair + " --image " + vwnImageName + " --user-data " + userDataPath + " --network " + networkName + " --config-drive " + str(
    configDrive) + " --availability-zone " + availabilityZone
propertyString = ""
for metadataitem in metadata.keys():
    propertyString += " --property '" + metadataitem + "'='" + metadata[metadataitem] + "'"

try:
    with open(jsonTrackingFile) as jsonfile:
        vwnJSON = json.load(jsonfile)
except IOError as e:
    vwnJSON = {}

vwnToCleanUp = []

for vwn in vwnJSON:
    present = False
    for node in nodelist:
        if vwn == node["Name"]:
            present = True
            break
    if present == False:
        vwnToCleanUp.append(vwn)

for vwn in vwnToCleanUp:
    del vwnJSON[vwn]

delete_errored_vm()

limits = json.loads(command_line("openstack limits show --absolute -f json"))
for limit in limits:
    if limit["Name"] == "totalCoresUsed":
        usedcores = limit["Value"]

print("Condor Used Cores: " + str(usedcores))

novacomputeservices = json.loads(command_line("openstack compute service list -f json"))

hypervisors = json.loads(command_line("openstack hypervisor list --long -f json"))

aggregateGroupsListPre = json.loads(command_line("openstack aggregate list --long -f json"))
aggregateGroupsList = {}
for aggregateGroup in aggregateGroupsListPre:
    totalCoresAvailable = 0
    totalCoresUsed = 0

    aggregateGroupsList[aggregateGroup["Name"]] = aggregateGroup

    aggregateGroupDetails = json.loads(command_line("openstack aggregate show " + aggregateGroup["Name"] + " -f json"))

    disabledCores = 0
    disabledStaticInstances = 0
    disableableStaticInstance = []

    # Check if nodes in json can be disabled if necessary
    for node in vwnJSON:
        if vwnJSON[node]["Aggregate"] == aggregateGroup["Name"]:
            if vwnJSON[node]["Disabled"] == "true":
                disabledCores += vwnJSON[node]["VCPUs"]
                if vwnJSON[node]["Type"] == "Static":
                    disabledStaticInstances += 1
            else:
                if vwnJSON[node]["Type"] == "Static":
                    disableableStaticInstance.append(node)

    print(aggregateGroup["Name"] + " Disabled Cores = " + str(disabledCores))
    print(aggregateGroup["Name"] + " Disabled Static Instances = " + str(disabledStaticInstances))
    print(aggregateGroup["Name"] + " Disableable Static Instance - " + str(disableableStaticInstance))

    # calculate vcpu's
    uphosts = []
    for host in aggregateGroupDetails["hosts"]:
        for novaservice in novacomputeservices:
            if novaservice["Status"] == "enabled" and novaservice["Host"] == host:
                for hv in hypervisors:
                    if hv["Hypervisor Hostname"] == novaservice["Host"]:
                        print["hv matched"]
                        totalCoresAvailable += hv["vCPUs"]
                        totalCoresUsed += hv["vCPUs Used"]

    # calculate buffers specifically for each aggregate
    availablecores = totalCoresAvailable - totalCoresUsed
    softcorebuffer = totalCoresAvailable / 10
    hardcorebuffer = softcorebuffer / 2

    print("Available cores: {} for aggregate {}".format(str(availablecores), aggregateGroup["Name"]))

    availablecondorcoressoft = availablecores - softcorebuffer
    availablecondorcoreshard = availablecores - hardcorebuffer

    print("{} - available to condor for aggregate {}".format(str(availablecondorcoressoft), aggregateGroup["Name"]))

    nameString = ""
    # disable disableable instances if the soft core buffer is hit
    if disableableStaticInstance != '':
        # delete the disableable instance if the hard core buffer is hit
        while (availablecondorcoreshard < 0):
            i = 0
            for disableStaticInstance in disableableStaticInstance:
                availablecondorcoreshard += vwnJSON[disableStaticInstance]["VCPUs"]
                availablecondorcoressoft += vwnJSON[disableStaticInstance]["VCPUs"]
                print("vm name:" + str(disableStaticInstance))
                print("Delete host:" + vwnJSON[disableStaticInstance]["IP Address"])
                command_line("openstack server delete " + str(disableStaticInstance))
                del vwnJSON[disableStaticInstance]
                del disableableStaticInstance[i]
                i += 1
                if availablecondorcoreshard > 0: break
            break

        while (availablecondorcoressoft < 0):
            for disableStaticInstance in disableableStaticInstance:
                print("Draining " + disableStaticInstance + " - " + vwnJSON[disableStaticInstance]["IP Address"])
                vwnJSON[disableStaticInstance]["Disabled"] = True
                print("Disable host:" + vwnJSON[disableStaticInstance]["IP Address"])
                disable_host(vwnJSON[disableStaticInstance]["IP Address"])
                availablecondorcoreshard += vwnJSON[disableStaticInstance]["VCPUs"]
                availablecondorcoressoft += vwnJSON[disableStaticInstance]["VCPUs"]
                if availablecondorcoressoft > 0: break
            break
    # check what sizes are available for the chosen aggregate group
    vcpus = 0
    count = 0
    if availablecondorcoressoft > 0:
        for aggregate in aggregates.keys():
            if aggregate == aggregateGroup["Name"]:
                for size in aggregates[aggregate].keys():
                    # make sure there is enough available cores for the chosen vm, if vcpu will be 0
                    if int(availablecondorcoressoft) >= int(size):
                        vcpus = size
                    else:
                        vcpus = 0
    else:
        vcpus = 0

    print(str(vcpus) + " chosen vcpus for aggregate group " + aggregateGroup["Name"])

    # Create a vm to fit
    if 0 < vcpus:
        # commented out the calculation to see how many vm's to create to fill all available space on the cloud
        # numberOfVWn = math.floor(int(availablecondorcoressoft) / int(vcpus))
        # create one vm per aggregate group per coyote run
        numberOfVWn = 1
        print("Number of VM's to create: " + str(numberOfVWn))
        for cores in range(int(numberOfVWn)):
            nowtime = strftime("%Y-%m-%d-%H-%M-%S", gmtime())
            for configSelect in aggregates.keys():
                if aggregateGroup["Name"] == configSelect:
                    for size in aggregates[configSelect].keys():
                        if str(size) == str(vcpus):
                            print("Aggregate group is " + configSelect)
                            print("size selected is: " + size + " flavor is " + aggregates[configSelect][size])
                            aggFlavor = aggregates[configSelect][size]
                            flavorString = " --flavor " + aggFlavor
                            flavorName = aggFlavor
                            nameString = vwnStaticNamePrefix
                            nameString += "-" + nowtime + "-" + str(count)
                            vwnType = "Static"

                            if nameString != "":
                                print("Creating new virtual workernode - " + nameString)
                                command_line(
                                    instanceCreateString + propertyString + flavorString + " " + nameString)
                                
                                with open(jsonTrackingFile, 'w') as jsonfile:
                                    json.dump(vwnJSON, jsonfile)

                            # details to be shown in the json file vwntracking
                            if nameString not in vwnJSON:
                                vwnJSON[nameString] = {"Status": "Active", "Disabled": False,
                                                       "FlavorID": flavorDict[flavorName]["ID"],
                                                       "Flavor": flavorName,
                                                       "VCPUs": flavorDict[flavorName]["VCPUs"],
                                                       "Type": vwnType,
                                                       "Aggregate": configSelect}

                            availablecondorcoressoft -= int(vcpus)
                            print("Available Condor cores left: " + str(availablecondorcoressoft))
                            count += 1
                            if count > 10:
                                availablecondorcoressoft = 0
                                vcpus = 0
                            sleep(10)
# if there are vm's that error remove them
delete_errored_vm()

with open(jsonTrackingFile, 'w') as jsonfile:
    json.dump(vwnJSON, jsonfile)
