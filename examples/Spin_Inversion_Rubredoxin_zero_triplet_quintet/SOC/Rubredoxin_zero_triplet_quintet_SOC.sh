#!/bin/bash
#PBS -N Rubredoxin_zero_triplet_quintet_SOC
#PBS -l nodes=1:ppn=12
#PBS -o Rubredoxin_zero_triplet_quintet_SOC.pbs.out
#PBS -e Rubredoxin_zero_triplet_quintet_SOC.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Rubredoxin_zero_triplet_quintet_SOC"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Rubredoxin_zero_triplet_quintet_SOC.inp > Rubredoxin_zero_triplet_quintet_SOC.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
