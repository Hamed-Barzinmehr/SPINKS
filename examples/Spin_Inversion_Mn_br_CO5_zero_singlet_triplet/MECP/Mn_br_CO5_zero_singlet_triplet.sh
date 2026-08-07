#!/bin/bash
#PBS -N Mn_CO
#PBS -l nodes=1:ppn=12
#PBS -o Mn_CO.pbs.out
#PBS -e Mn_CO.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Mn_CO"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Mn_CO.inp > Mn_CO.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
