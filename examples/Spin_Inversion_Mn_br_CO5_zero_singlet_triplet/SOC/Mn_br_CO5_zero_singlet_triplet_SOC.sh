#!/bin/bash
#PBS -N Mn_br_CO5_zero_singlet_triplet_SOC
#PBS -l nodes=1:ppn=12
#PBS -o Mn_br_CO5_zero_singlet_triplet_SOC.pbs.out
#PBS -e Mn_br_CO5_zero_singlet_triplet_SOC.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Mn_br_CO5_zero_singlet_triplet_SOC"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Mn_br_CO5_zero_singlet_triplet_SOC.inp > Mn_br_CO5_zero_singlet_triplet_SOC.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
