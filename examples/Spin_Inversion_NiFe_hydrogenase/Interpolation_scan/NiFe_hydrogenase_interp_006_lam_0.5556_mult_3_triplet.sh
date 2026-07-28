#!/bin/bash
#PBS -N Ni_Fe_interp_006_lam_0.5556_mult_3_triplet
#PBS -l nodes=1:ppn=12
#PBS -o Ni_Fe_interp_006_lam_0.5556_mult_3_triplet.pbs.out
#PBS -e Ni_Fe_interp_006_lam_0.5556_mult_3_triplet.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Ni_Fe_interp_006_lam_0.5556_mult_3_triplet"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Ni_Fe_interp_006_lam_0.5556_mult_3_triplet.inp > Ni_Fe_interp_006_lam_0.5556_mult_3_triplet.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
