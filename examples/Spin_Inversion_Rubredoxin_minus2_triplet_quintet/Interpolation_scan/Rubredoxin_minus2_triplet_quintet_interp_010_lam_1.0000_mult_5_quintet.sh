#!/bin/bash
#PBS -N Rubredoxin_minus2_triplet_quintet_interp_010_lam_1.0000_mult_5_quintet
#PBS -l nodes=1:ppn=12
#PBS -o Rubredoxin_minus2_triplet_quintet_interp_010_lam_1.0000_mult_5_quintet.pbs.out
#PBS -e Rubredoxin_minus2_triplet_quintet_interp_010_lam_1.0000_mult_5_quintet.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Rubredoxin_minus2_triplet_quintet_interp_010_lam_1.0000_mult_5_quintet"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Rubredoxin_minus2_triplet_quintet_interp_010_lam_1.0000_mult_5_quintet.inp > Rubredoxin_minus2_triplet_quintet_interp_010_lam_1.0000_mult_5_quintet.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
