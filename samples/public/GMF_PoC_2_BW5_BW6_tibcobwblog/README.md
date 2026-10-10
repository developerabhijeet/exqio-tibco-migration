# PoC sample 2: tibcobwblog samples (BusinessWorks 5 and 6)

Source: github.com/tibcobwblog/tibcobwblog_samples.
Licence: none declared in the repository. Use for internal learning and parser testing only; do not put these files in customer-facing material.

## What is inside
- bw5.x: 16 BW5 projects (30 .process files): JMS queue and selector, ActiveMQ request-reply, REST and SOAP, SSL, Java Code activity, file write, loops, catch, shared variables
- bw6.x: 8 BW6 projects (11 .bwp files): dynamic subprocess calls, Java invoke, HTTPS, mail, external web service, activator process

## Useful for the PoC
- TIBCO_BW_JMS_SELECTOR/CleanMyMessages.process: JMS get message, loop, catch, file write (EMS to Service Bus patterns)
- TIBCO_BW_REST_EXAMPLE/Processes: subprocess chain (MainProcessStarter calls ConcatProcess, GetProcessName, Utils/ParseQueryString)
- TIBCO_BW_AMQ_REQRPLY/Processes/AQM_REQRPLY.process: timer starter plus JMS request-reply, the smallest BW5 example
- TIBCO_BW_JAVA_CODE_FIB: Java Code activity inside a process
- Each BW5 project shows the standard layout: Processes/, SharedResources/, AESchemas/, defaultVars/ (global variables), vcrepo.dat
