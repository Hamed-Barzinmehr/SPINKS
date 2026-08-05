#!/bin/bash
#PBS -N Sing_Q_negative_2
#PBS -l nodes=1:ppn=12
#PBS -o Sing_Q_negative_2.pbs.out
#PBS -e Sing_Q_negative_2.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Sing_Q_negative_2"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Sing_Q_negative_2.inp > Sing_Q_negative_2.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
