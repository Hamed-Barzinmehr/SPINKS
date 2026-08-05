#!/bin/bash
#PBS -N T_Q_Zero
#PBS -l nodes=1:ppn=12
#PBS -o T_Q_Zero.pbs.out
#PBS -e T_Q_Zero.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: T_Q_Zero"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca T_Q_Zero.inp > T_Q_Zero.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
