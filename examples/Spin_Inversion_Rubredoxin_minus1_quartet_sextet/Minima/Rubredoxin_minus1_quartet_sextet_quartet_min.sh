#!/bin/bash
#PBS -N Qu_sex_nagative_1_quartet_min
#PBS -l nodes=1:ppn=12
#PBS -o Qu_sex_nagative_1_quartet_min.pbs.out
#PBS -e Qu_sex_nagative_1_quartet_min.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Qu_sex_nagative_1_quartet_min"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Qu_sex_nagative_1_quartet_min.inp > Qu_sex_nagative_1_quartet_min.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
