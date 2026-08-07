#!/bin/bash
#PBS -N Sing_Q_Zero_ROHF_HS
#PBS -l nodes=1:ppn=12
#PBS -o Sing_Q_Zero_ROHF_HS.pbs.out
#PBS -e Sing_Q_Zero_ROHF_HS.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Sing_Q_Zero_ROHF_HS"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Sing_Q_Zero_ROHF_HS.inp > Sing_Q_Zero_ROHF_HS.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
