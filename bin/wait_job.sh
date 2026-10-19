#!/bin/bash
# Block until a Slurm job leaves the queue, then print its final state and
# the tail of a log file.   bin/wait_job.sh <jobid> [logfile] [poll seconds]
job="$1"; logf="${2:-}"; poll="${3:-120}"
while squeue -h -j "$job" -o %T 2>/dev/null | grep -qE 'PENDING|RUNNING|COMPLETING|CONFIGURING'; do
    sleep "$poll"
done
sacct -j "$job" -X -o JobID,State,Elapsed,ExitCode -n 2>/dev/null
if [ -n "$logf" ] && [ -f "$logf" ]; then
    echo "--- tail of $logf"
    tail -n 40 "$logf"
fi
