#!/bin/bash
#PBS -N Cobalt_acetate_plus1_singlet_triplet_SOC
#PBS -l nodes=1:ppn=12
#PBS -o Cobalt_acetate_plus1_singlet_triplet_SOC.pbs.out
#PBS -e Cobalt_acetate_plus1_singlet_triplet_SOC.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Cobalt_acetate_plus1_singlet_triplet_SOC"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Cobalt_acetate_plus1_singlet_triplet_SOC.inp > Cobalt_acetate_plus1_singlet_triplet_SOC.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
