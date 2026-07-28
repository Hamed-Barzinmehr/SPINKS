#!/bin/bash
#PBS -N D_S_negative_1_doublet_min
#PBS -l nodes=1:ppn=12
#PBS -o D_S_negative_1_doublet_min.pbs.out
#PBS -e D_S_negative_1_doublet_min.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: D_S_negative_1_doublet_min"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca D_S_negative_1_doublet_min.inp > D_S_negative_1_doublet_min.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
