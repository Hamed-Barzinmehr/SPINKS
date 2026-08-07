#!/bin/bash
#PBS -N Cobalt_INT2_interp_005_lam_0.4444_mult_3_triplet
#PBS -l nodes=1:ppn=12
#PBS -o Cobalt_INT2_interp_005_lam_0.4444_mult_3_triplet.pbs.out
#PBS -e Cobalt_INT2_interp_005_lam_0.4444_mult_3_triplet.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Cobalt_INT2_interp_005_lam_0.4444_mult_3_triplet"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Cobalt_INT2_interp_005_lam_0.4444_mult_3_triplet.inp > Cobalt_INT2_interp_005_lam_0.4444_mult_3_triplet.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
