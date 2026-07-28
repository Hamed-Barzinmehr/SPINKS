#!/bin/bash
#PBS -N T_Q_Zero_quintet_min
#PBS -l nodes=1:ppn=12
#PBS -o T_Q_Zero_quintet_min.pbs.out
#PBS -e T_Q_Zero_quintet_min.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: T_Q_Zero_quintet_min"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca T_Q_Zero_quintet_min.inp > T_Q_Zero_quintet_min.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
