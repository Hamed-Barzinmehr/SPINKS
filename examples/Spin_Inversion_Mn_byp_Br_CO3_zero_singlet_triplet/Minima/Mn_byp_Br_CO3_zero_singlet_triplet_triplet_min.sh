#!/bin/bash
#PBS -N Mn_by_triplet_min
#PBS -l nodes=1:ppn=12
#PBS -o Mn_by_triplet_min.pbs.out
#PBS -e Mn_by_triplet_min.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Mn_by_triplet_min"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Mn_by_triplet_min.inp > Mn_by_triplet_min.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
