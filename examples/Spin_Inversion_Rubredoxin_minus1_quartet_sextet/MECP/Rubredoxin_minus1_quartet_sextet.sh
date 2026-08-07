#!/bin/bash
#PBS -N Qu_sex_nagative_1
#PBS -l nodes=1:ppn=12
#PBS -o Qu_sex_nagative_1.pbs.out
#PBS -e Qu_sex_nagative_1.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Qu_sex_nagative_1"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Qu_sex_nagative_1.inp > Qu_sex_nagative_1.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
